"""
app.py — Modelo de riesgo TRC (Magerit 3.0)

Aplicación Streamlit para:
  - dar de alta, editar y eliminar activos y su valoración (subtipos
    seleccionables de un catálogo controlado, no texto libre),
  - definir y eliminar dependencias, con vista por activo y árbol de
    dependencias descendente,
  - definir personas asociadas,
  - recalcular y consultar amenazas (directas y heredadas) e impacto
    (vista resumen tipo PILAR con detalle al seleccionar un activo).

Requiere en .streamlit/secrets.toml (o variables de entorno equivalentes):
    SUPABASE_URL = "https://xxxx.supabase.co"
    SUPABASE_SERVICE_ROLE_KEY = "..."   # clave service_role (RLS activado en las tablas, 09/09/2026)

Requiere también haber ejecutado migracion_v2_subtipos_catalogo.sql
(añade la tabla subtipos_catalogo) y migracion_multiproyecto.sql
(añade la tabla proyectos + activos.proyecto_id) sobre la base de datos.
El árbol de dependencias usa st.graphviz_chart, que necesita el binario
`graphviz` instalado en el sistema (ver packages.txt si se despliega en
Streamlit Community Cloud).
"""

from collections import defaultdict
import re

import streamlit as st
import pandas as pd
from supabase import create_client

from calculo import (
    ejecutar_recalculo, construir_adj_forward, dependencias_transitivas,
    maximo_por_activo, DIMENSIONES,
)

st.set_page_config(page_title="Modelo de riesgo TRC", layout="wide")

TIPOS_MAGERIT = ["INFORMACION", "SERVICIO", "SOFTWARE", "EQUIPAMIENTO",
                  "COMUNICACIONES", "INSTALACIONES", "PERSONAL"]
ROLES = ["Usuario", "Operador", "Administrador", "Desarrollador", "Responsable"]
VALORACION_OPCIONES = ["n.a", "0", "Bajo", "Medio", "Alto"]
VALORACION_A_NUMERO = {"n.a": None, "0": 0.0, "Bajo": 1.5, "Medio": 4.0, "Alto": 7.0}
NUMERO_A_VALORACION = {None: "n.a", 0.0: "0", 1.5: "Bajo", 4.0: "Medio", 7.0: "Alto"}

# Cortes de color para la vista de Impacto — los mismos que separan las
# categorías de valoración de activos (Bajo=1.5, Medio=4.0, Alto=7.0).
CORTE_BAJO, CORTE_MEDIO, CORTE_ALTO = 1.5, 4.0, 7.0


@st.cache_resource
def get_client():
    return create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_SERVICE_ROLE_KEY"])


sb = get_client()


# ---------------------------------------------------------------------
# Lectura de datos (sin cachear: siempre al día, la app es de uso interno)
# ---------------------------------------------------------------------
def cargar_proyectos():
    res = sb.table("proyectos").select("*").order("nombre").execute()
    return res.data


def cargar_activos(proyecto_id):
    res = sb.table("activos").select("*, activo_subtipos(subtipo)") \
        .eq("proyecto_id", proyecto_id).execute()
    activos = {}
    id_to_codigo = {}
    for row in res.data:
        subtipos = ";".join(s["subtipo"] for s in row.get("activo_subtipos", []))
        activos[row["codigo"]] = {
            "id": row["id"], "codigo": row["codigo"], "nombre": row["nombre"],
            "tipo": row["tipo"], "subtipo": subtipos,
            "valor_propio": {
                "D": row["valor_propio_d"], "I": row["valor_propio_i"],
                "C": row["valor_propio_c"], "A": row["valor_propio_a"], "T": row["valor_propio_t"],
            },
        }
        id_to_codigo[row["id"]] = row["codigo"]
    return activos, id_to_codigo


def cargar_dependencias(id_to_codigo):
    """Solo dependencias cuyo activo superior pertenece al proyecto activo
    (dependencias no tiene proyecto_id propio: se filtra por los activos
    del proyecto, vía id_to_codigo, que ya viene filtrado por proyecto)."""
    ids_proyecto = list(id_to_codigo.keys())
    if not ids_proyecto:
        return [], []
    res = sb.table("dependencias").select("*").in_("activo_superior_id", ids_proyecto).execute()
    return [(id_to_codigo[r["activo_superior_id"]], id_to_codigo[r["activo_inferior_id"]], r["grado"])
            for r in res.data], res.data


def cargar_personas(id_to_codigo):
    """Igual que cargar_dependencias: personas_asociadas no tiene
    proyecto_id propio, se filtra por los activos del proyecto activo."""
    ids_proyecto = list(id_to_codigo.keys())
    if not ids_proyecto:
        return [], []
    res = sb.table("personas_asociadas").select("*").in_("activo_id", ids_proyecto).execute()
    return [(id_to_codigo[r["activo_id"]], r["persona_nombre"], r["tipo_rol"], r["grado"])
            for r in res.data], res.data


def cargar_catalogo(tamano_empresa, aplicar_contexto=True):
    """
    tamano_empresa: 'Pequeña empresa' / 'Mediana empresa' / 'Gran empresa'
    (clave de factor_tamano_empresa). El sector queda fijo por ahora
    (Banca / Infraestructuras financieras -- unica fila en
    factor_sectorial hasta que se investigue otro sector).

    aplicar_contexto=False: no consulta factor_sectorial ni
    factor_tamano_empresa -- toda amenaza sale con fs=1.0/fe=1.0 (ARO
    puro del catalogo TRCv4, sin ningun ajuste sectorial/de tamaño).
    """
    if aplicar_contexto:
        res_fs = sb.table("factor_sectorial").select("categoria_id, fs").execute()
        fs_por_categoria = {r["categoria_id"]: r["fs"] for r in res_fs.data}

        res_fe = sb.table("factor_tamano_empresa").select("categoria_id, fe") \
            .eq("tamano_empresa", tamano_empresa).execute()
        fe_por_categoria = {r["categoria_id"]: r["fe"] for r in res_fe.data}
    else:
        fs_por_categoria = {}
        fe_por_categoria = {}

    res = sb.table("catalogo_amenazas").select(
        "*, categorias_amenaza(nombre), amenaza_tipo_activo(tipo_activo)"
    ).eq("vigente", True).execute()
    catalogo = []
    for row in res.data:
        cat = row.get("categorias_amenaza") or {}
        cat_id = row["categoria_id"]
        catalogo.append({
            "amenaza": row["amenaza"],
            "categoria": cat.get("nombre"),
            "fs": fs_por_categoria.get(cat_id, 1.0),
            "fe": fe_por_categoria.get(cat_id, 1.0),
            "subcategoria": row["subcategoria"],
            "probabilidad": row["probabilidad"],
            "D": row["degradacion_d"], "I": row["degradacion_i"], "C": row["degradacion_c"],
            "A": row["degradacion_a"], "T": row["degradacion_t"],
            "tipos_activo": [t["tipo_activo"] for t in row.get("amenaza_tipo_activo", [])],
        })
    return catalogo


def cargar_subtipos_catalogo():
    """{tipo_magerit: [subtipo, ...]} ordenado, desde la tabla subtipos_catalogo."""
    res = sb.table("subtipos_catalogo").select("*").execute()
    por_tipo = defaultdict(list)
    for r in res.data:
        por_tipo[r["tipo_magerit"]].append(r["subtipo"])
    return {k: sorted(v) for k, v in por_tipo.items()}


def cargar_tipos_por_categoria():
    """
    {categoria_nombre: {tipo_activo, ...}} -- que tipos de activo reciben
    amenazas de cada categoria, segun el catalogo TRCv4 ya cargado
    (catalogo_amenazas -> categorias_amenaza / amenaza_tipo_activo).
    Se usa para no mostrar, al asignar una salvaguarda, activos de un
    tipo al que esa categoria de amenaza nunca llega.
    """
    res = sb.table("catalogo_amenazas").select(
        "categorias_amenaza(nombre), amenaza_tipo_activo(tipo_activo)"
    ).eq("vigente", True).execute()
    tipos_por_categoria = defaultdict(set)
    for row in res.data:
        cat = (row.get("categorias_amenaza") or {}).get("nombre")
        if not cat:
            continue
        for t in row.get("amenaza_tipo_activo", []):
            tipos_por_categoria[cat].add(t["tipo_activo"])
    return dict(tipos_por_categoria)


def cargar_salvaguardas():
    """
    Devuelve (salvaguardas, categorias_por_salvaguarda, asignaciones):
    - salvaguardas: {id: {nombre, tipo, familia, eficacia_impacto, eficacia_probabilidad, descripcion}}
    - categorias_por_salvaguarda: {salvaguarda_id: [nombre_categoria, ...]}
    - asignaciones: lista de dict con activo_id, salvaguarda_id, familia_override,
      nivel_madurez, coverage, reliability (tal cual en activo_salvaguardas)
    """
    res_s = sb.table("salvaguardas").select("*").execute()
    salvaguardas = {r["id"]: r for r in res_s.data}

    res_sc = sb.table("salvaguarda_categoria").select(
        "salvaguarda_id, categorias_amenaza(nombre)"
    ).execute()
    categorias_por_salvaguarda = defaultdict(list)
    for r in res_sc.data:
        cat = r.get("categorias_amenaza") or {}
        if cat.get("nombre"):
            categorias_por_salvaguarda[r["salvaguarda_id"]].append(cat["nombre"])

    res_as = sb.table("activo_salvaguardas").select("*").execute()
    asignaciones = [{
        "activo_id": r["activo_id"], "salvaguarda_id": r["salvaguarda_id"],
        "familia_override": r.get("familia_override"),
        "nivel_madurez": r.get("nivel_madurez", 5),
        "coverage": r.get("coverage", 100), "reliability": r.get("reliability", 100),
    } for r in res_as.data]

    return salvaguardas, dict(categorias_por_salvaguarda), asignaciones


# Tabla de conversion Nivel de madurez (L0-L5) -> (Coverage%, Reliability%),
# UNA POR CADA FAMILIA de implantacion. Capability = ei/ep nominal del
# catalogo (FAIR-CAM Standard Artifact v1.0, Sec.2.4: Capability x Coverage
# x Reliability = Control Maturity, cita literal). Los 18 valores de esta
# tabla NO tienen fuente publica (se busco expresamente y no existe, ver
# Metodologia_Madurez_Salvaguardas_FAIRCAM.md) -- son estimacion de
# proyecto, punto de partida a refinar con datos propios observados.
TABLAS_FAMILIA = {
    "Automatizada": {0: (0, 0), 1: (35, 45), 2: (55, 65), 3: (75, 85), 4: (90, 95), 5: (100, 100)},
    "Semi":         {0: (0, 0), 1: (30, 35), 2: (50, 55), 3: (70, 75), 4: (85, 90), 5: (100, 100)},
    "Manual":       {0: (0, 0), 1: (25, 25), 2: (45, 45), 3: (65, 60), 4: (80, 75), 5: (100, 90)},
}
NOMBRE_FAMILIA = {
    "Automatizada": "Automatizada / Técnica", "Semi": "Semi-automatizada", "Manual": "Manual / Procedimental",
}
NOMBRE_NIVEL_MADUREZ = {
    0: "L0 — Inexistente", 1: "L1 — Inicial/ad hoc", 2: "L2 — Reproducible pero intuitivo",
    3: "L3 — Proceso definido", 4: "L4 — Gestionado y medible", 5: "L5 — Optimizado",
}

# Orden de bloques y familias del ENS (CCN-STIC-804, Guía de Implantación, y
# confirmado para "op.nub" -- ausente en la 804 de 2017 -- contra el orden
# real que usan las guías vigentes CCN-STIC 825/886/887/888: siempre justo
# despues de op.ext.4, antes de op.cont).
ORDEN_FAMILIAS_ENS = [
    "org",
    "op.pl", "op.acc", "op.exp", "op.ext", "op.nub", "op.cont", "op.mon",
    "mp.if", "mp.per", "mp.eq", "mp.com", "mp.si", "mp.sw", "mp.info", "mp.s",
]


def clave_orden_804(nombre_salvaguarda):
    """Ordena '[familia.numero] Nombre' segun el orden oficial de bloques/
    familias del ENS, y dentro de cada familia por el numero ascendente."""
    m = re.match(r"\[([a-z]+(?:\.[a-z]+)?)\.(\d+)\]", nombre_salvaguarda or "")
    if not m:
        return (999, 999)
    familia, numero = m.group(1), int(m.group(2))
    try:
        idx = ORDEN_FAMILIAS_ENS.index(familia)
    except ValueError:
        idx = 999
    return (idx, numero)


def construir_eficacias_por_activo_categoria(id_to_codigo):
    """
    {(codigo_activo, categoria): [(ei, ep), ...]} -- lo que espera
    ejecutar_recalculo(). Cruza asignaciones (activo<->salvaguarda) con las
    categorias que protege cada salvaguarda, aplicando Coverage/Reliability
    YA RESUELTOS de cada asignacion concreta (ya sea el valor por defecto de
    su nivel/familia, o el que el analista haya editado a mano -- ese
    calculo ya se hizo al guardar la asignacion, aqui solo se usa el
    resultado guardado).

    eficacia_efectiva = Capability(ei/ep nominal) x Coverage x Reliability
    (FAIR-CAM Standard Artifact v1.0, Sec.2.4)
    """
    salvaguardas, categorias_por_salvaguarda, asignaciones = cargar_salvaguardas()
    resultado = defaultdict(list)
    for asig in asignaciones:
        s = salvaguardas.get(asig["salvaguarda_id"])
        cod = id_to_codigo.get(asig["activo_id"])
        if s is None or cod is None:
            continue
        factor = (asig["coverage"] / 100.0) * (asig["reliability"] / 100.0)
        ei = (s["eficacia_impacto"] or 0) * factor
        ep = (s["eficacia_probabilidad"] or 0) * factor
        for categoria in categorias_por_salvaguarda.get(asig["salvaguarda_id"], []):
            resultado[(cod, categoria)].append((ei, ep))
    return dict(resultado)


def anadir_subtipo_catalogo(subtipo, tipo):
    sb.table("subtipos_catalogo").insert(
        {"subtipo": subtipo.strip().upper(), "tipo_magerit": tipo}
    ).execute()


def idx_valoracion(valor):
    """Índice en VALORACION_OPCIONES para un valor numérico ya guardado."""
    label = NUMERO_A_VALORACION.get(valor, "n.a")
    return VALORACION_OPCIONES.index(label)


def recalcular_todo(activos, id_to_codigo):
    """Recalcula valor acumulado, amenazas directas y filas de propagación,
    y los deja en session_state para que las páginas Amenazas e Impacto
    los reutilicen sin tener que volver a pulsar el botón en cada una."""
    dep_edges, _ = cargar_dependencias(id_to_codigo)
    per_edges, _ = cargar_personas(id_to_codigo)
    catalogo = cargar_catalogo(
        st.session_state["tamano_empresa"], st.session_state["aplicar_contexto"]
    )
    eficacias = construir_eficacias_por_activo_categoria(id_to_codigo)
    try:
        with st.spinner("Propagando valor y calculando impacto..."):
            valor_acum, amenazas_directas, filas_prop = ejecutar_recalculo(
                activos, dep_edges, per_edges, catalogo, eficacias
            )
        st.session_state["valor_acum"] = valor_acum
        st.session_state["amenazas_directas"] = amenazas_directas
        st.session_state["filas_prop"] = filas_prop
        st.success("Recalculado.")
    except ValueError as e:
        st.error(str(e))

def render_pagina_metrica(nombre_metrica, campo_cualitativo, key_prefix, subtitulo):
    """
    Página compartida para Impacto y Riesgo -- misma estructura para
    las dos (Potencial/Actual x Acumulado/Repercutido + detalle por
    dimensión), variando solo el campo que se muestra. Solo se muestra
    la vista CUALITATIVA (Metodologia_App_Riesgos.md §6.6, decision
    expresa del equipo): el Cuantitativo ya se calcula en calculo.py
    pero no se expone aqui todavia -- ver el campo "{dim}_impacto"/
    "{dim}_riesgo" (sin "_cualitativo") en session_state["filas_prop"]
    si se decide reactivarlo mas adelante.
    """
    st.title(nombre_metrica)
    st.caption(subtitulo)

    if st.button("🔄 Recalcular", type="primary", key=f"recalc_{key_prefix}"):
        recalcular_todo(activos, id_to_codigo)

    if "filas_prop" not in st.session_state:
        st.info("Pulsa Recalcular para ver los resultados.")
        return

    # Color por banda de valor (0-10), misma escala que usa Magerit para
    # el valor/impacto de activos (Libro II cap. 4, §1.4): 0=Despreciable,
    # 1-2=Bajo, 3-5=Medio, 6-8=Alto, 9-10=Muy Alto/Extremo.
    def color_valor(val):
        if pd.isna(val):
            return ""
        if val <= 0:
            return "background-color: #f3f4f6; color: #111827"
        if val <= 2:
            return "background-color: #d1fae5; color: #111827"
        if val <= 5:
            return "background-color: #fef9c3; color: #111827"
        if val <= 8:
            return "background-color: #fed7aa; color: #111827"
        return "background-color: #fecaca; color: #111827"

    # --- Selector Potencial / Actual --------------------------------------
    # Potencial = sin salvaguardas (Metodologia_App_Riesgos.md, campo base).
    # Actual = con las salvaguardas asignadas a cada activo, buscadas en el
    # ORIGEN de cada amenaza (se propaga por la cadena de dependencia).
    vista = st.radio(
        "Vista", ["Potencial (sin salvaguardas)", "Actual (con salvaguardas)"],
        captions=["El riesgo si no hubiera ninguna protección desplegada.",
                  "El riesgo real de hoy, descontando las salvaguardas ya asignadas."],
        horizontal=True, key=f"{key_prefix}_vista",
    )
    campo_activo = campo_cualitativo if vista.startswith("Potencial") else f"{campo_cualitativo}_actual"

    # --- Submenú Acumulado / Repercutido ---------------------------------
    # Acumulado = amenazas Directas (valor ACUMULADO del activo).
    # Repercutido = heredadas por Dependencia/Persona asociada (valor
    # PROPIO del activo destino). Metodologia_App_Riesgos.md §6.3.1-6.3.4.
    submenu = st.radio(
        "Origen de la amenaza", ["Acumulado (amenazas directas)", "Repercutido (heredadas)"],
        captions=["Amenazas propias de este activo, incluyendo lo que depende de él.",
                  "Amenazas que este activo hereda de otro del que depende."],
        horizontal=True, key=f"{key_prefix}_submenu",
    )
    es_acumulado = submenu.startswith("Acumulado")
    if es_acumulado:
        st.caption("Amenazas propias del activo (origen Directa), con el valor ACUMULADO.")
        filas_metrica = [f for f in st.session_state["filas_prop"] if f["origen"] == "Directa"]
    else:
        st.caption("Amenazas heredadas por dependencia o persona asociada, con el valor "
                   "PROPIO del activo destino.")
        filas_metrica = [f for f in st.session_state["filas_prop"] if f["origen"] != "Directa"]

    # --- Resumen por activo (máximo entre amenazas de este origen) ------
    resumen = maximo_por_activo(filas_metrica, campo_activo)

    filas_resumen = [{
        "Código": cod, "Nombre": activos[cod]["nombre"], "Tipo": activos[cod]["tipo"],
        nombre_metrica: resumen[cod]["valor"] if cod in resumen else None,
    } for cod in opciones_codigo]
    df_resumen = pd.DataFrame(filas_resumen).sort_values(
        nombre_metrica, ascending=False, na_position="last"
    ).reset_index(drop=True)

    st.caption(f"{nombre_metrica} máximo (método Cualitativo, {vista.split(' ')[0]}), entre las amenazas "
               "de este origen y las dimensiones de cada activo. Selecciona una fila para ver el detalle.")

    evento = st.dataframe(
        df_resumen.style.map(color_valor, subset=[nombre_metrica])
                  .format({nombre_metrica: lambda v: "—" if pd.isna(v) else f"{v:.2f}"}),
        use_container_width=True, hide_index=True,
        on_select="rerun", selection_mode="single-row", key=f"tabla_{key_prefix}",
    )

    filas_seleccionadas = evento.selection.rows if evento and evento.selection else []
    if filas_seleccionadas and not df_resumen.empty:
        cod_detalle = df_resumen.iloc[filas_seleccionadas[0]]["Código"]
    elif not df_resumen.empty:
        cod_detalle = df_resumen.iloc[0]["Código"]
    else:
        cod_detalle = None

    st.subheader("Detalle por dimensión")
    if cod_detalle is None:
        st.caption("No hay activos que mostrar.")
        return

    st.markdown(f"**{cod_detalle} · {activos[cod_detalle]['nombre']}**")
    detalle = [f for f in filas_metrica if f["codigo"] == cod_detalle]

    filas_det = []
    for f in detalle:
        valores_dim = {dim: f.get(f"{dim}_{campo_activo}") for dim in DIMENSIONES}
        if all(v is None for v in valores_dim.values()):
            continue
        fila_out = {"Origen": f["origen"] if es_acumulado else f["detalle_origen"],
                    "Amenaza": f["amenaza"], "Probabilidad": f["probabilidad"]}
        for dim in DIMENSIONES:
            fila_out[dim] = valores_dim[dim]
        fila_out["_max"] = max((v for v in valores_dim.values() if v is not None), default=0)
        filas_det.append(fila_out)

    if not filas_det:
        st.caption(f"Sin amenazas de este origen con {nombre_metrica} calculado.")
        return

    df_det = pd.DataFrame(filas_det).sort_values(
        "_max", ascending=False).drop(columns="_max").reset_index(drop=True)
    st.dataframe(
        df_det.style.map(color_valor, subset=DIMENSIONES)
              .format({d: (lambda v: "—" if pd.isna(v) else f"{v:.2f}") for d in DIMENSIONES}),
        use_container_width=True, hide_index=True,
    )


# ---------------------------------------------------------------------
# Selección de proyecto (barra lateral) -- cada proyecto tiene sus propios
# activos/dependencias/personas; el catálogo de amenazas y las salvaguardas
# son comunes a todos.
# ---------------------------------------------------------------------
proyectos = cargar_proyectos()
if not proyectos:
    st.error("No hay ningún proyecto creado. Ejecuta migracion_multiproyecto.sql "
              "sobre la base de datos antes de continuar.")
    st.stop()

nombres_proyecto = [p["nombre"] for p in proyectos]
indice_proyecto_defecto = (
    nombres_proyecto.index(st.session_state["proyecto_nombre"])
    if st.session_state.get("proyecto_nombre") in nombres_proyecto else 0
)
proyecto_sel_nombre = st.sidebar.selectbox(
    "📁 Proyecto", nombres_proyecto, index=indice_proyecto_defecto, key="selector_proyecto",
)
proyecto_actual = next(p for p in proyectos if p["nombre"] == proyecto_sel_nombre)
st.session_state["proyecto_nombre"] = proyecto_actual["nombre"]
st.session_state["proyecto_id"] = proyecto_actual["id"]

# Cambiar de proyecto invalida los resultados de recálculo del proyecto
# anterior (son específicos de sus activos/dependencias/personas).
if st.session_state.get("_proyecto_anterior") not in (None, proyecto_actual["id"]):
    for _key in ["valor_acum", "amenazas_directas", "filas_prop"]:
        st.session_state.pop(_key, None)
st.session_state["_proyecto_anterior"] = proyecto_actual["id"]

st.sidebar.divider()

# ---------------------------------------------------------------------
# Contexto del proyecto (Factor de Riesgo Contextual)
# ---------------------------------------------------------------------
# Sector fijo por ahora (Banca / Infraestructuras financieras -- unico
# investigado). Tamaño de empresa SI es seleccionable en caliente: cambia
# que FE se aplica al ARO de cada amenaza en el proximo Recalcular.
TAMANOS_EMPRESA = ["Pequeña empresa", "Mediana empresa", "Gran empresa"]
if "tamano_empresa" not in st.session_state:
    st.session_state["tamano_empresa"] = "Mediana empresa"
if "aplicar_contexto" not in st.session_state:
    st.session_state["aplicar_contexto"] = True

st.sidebar.toggle(
    "Aplicar Factor de Riesgo Contextual", key="aplicar_contexto",
    help="Si está desactivado, el Riesgo usa el ARO puro del catálogo TRCv4 "
         "(FS=1.0, FE=1.0 para todas las amenazas), sin ningún ajuste de "
         "sector ni tamaño de empresa. Pulsa Recalcular después de cambiarlo.",
)

st.sidebar.selectbox(
    "Tamaño de empresa", TAMANOS_EMPRESA,
    index=TAMANOS_EMPRESA.index(st.session_state["tamano_empresa"]),
    key="tamano_empresa",
    disabled=not st.session_state["aplicar_contexto"],
    help="Sector fijo: Banca / Infraestructuras financieras. Afecta al "
         "Factor de Riesgo Contextual (FE) del Riesgo Potencial. "
         "Pulsa Recalcular después de cambiarlo.",
)
if st.session_state["aplicar_contexto"]:
    st.sidebar.caption("Sector: Banca / Infraestructuras financieras")
else:
    st.sidebar.caption("Factor de Riesgo Contextual desactivado — ARO puro del catálogo.")
st.sidebar.divider()

# ---------------------------------------------------------------------
# Navegación
# ---------------------------------------------------------------------
pagina = st.sidebar.radio(
    "Navegación",
    ["Proyectos", "Activos", "Dependencias", "Personas asociadas", "Salvaguardas", "Amenazas", "Impacto", "Riesgo"],
)

# Cambiar de página cancela cualquier edición en curso (activo o dependencia),
# para no dejar un formulario "a medias" abierto al volver más tarde.
if st.session_state.get("_pagina_anterior") not in (None, pagina):
    for _key in ["dep_editando", "accion_activo", "accion_activo_cod"]:
        st.session_state.pop(_key, None)
st.session_state["_pagina_anterior"] = pagina

activos, id_to_codigo = cargar_activos(st.session_state["proyecto_id"])
opciones_codigo = sorted(activos.keys())
subtipos_por_tipo = cargar_subtipos_catalogo()


# ---------------------------------------------------------------------
# Página: Proyectos
# ---------------------------------------------------------------------
if pagina == "Proyectos":
    st.title("Proyectos")
    st.caption("Cada proyecto tiene sus propios activos, dependencias y personas asociadas. "
               "El catálogo de amenazas y las salvaguardas son comunes a todos los proyectos.")

    st.subheader(f"Proyectos existentes ({len(proyectos)})")
    filas_p = [{"Nombre": p["nombre"], "Sector": p.get("sector"), "Tamaño": p.get("tamano"),
                "Descripción": p.get("descripcion")} for p in proyectos]
    st.dataframe(pd.DataFrame(filas_p), use_container_width=True, hide_index=True)

    st.markdown("---")
    st.subheader("Eliminar proyecto")
    if len(proyectos) <= 1:
        st.caption("No se puede eliminar el único proyecto que queda.")
    else:
        nombre_eliminar = st.selectbox(
            "Selecciona el proyecto a eliminar", nombres_proyecto, key="proyecto_a_eliminar",
        )
        proyecto_eliminar = next(p for p in proyectos if p["nombre"] == nombre_eliminar)
        n_activos_p = sb.table("activos").select("id", count="exact") \
            .eq("proyecto_id", proyecto_eliminar["id"]).execute().count or 0
        if n_activos_p:
            st.warning(
                f"Este proyecto tiene {n_activos_p} activo(s); se eliminarán también "
                "sus dependencias, personas asociadas y salvaguardas asignadas."
            )
        confirmar_borrado_p = st.checkbox(
            f"Sí, entiendo que se borrará \"{nombre_eliminar}\" y todo lo que contiene",
            key="confirmar_borrado_proyecto",
        )
        if st.button("🗑️ Eliminar proyecto", disabled=not confirmar_borrado_p):
            sb.table("proyectos").delete().eq("id", proyecto_eliminar["id"]).execute()
            st.success(f"Proyecto {nombre_eliminar} eliminado.")
            if st.session_state.get("proyecto_nombre") == nombre_eliminar:
                st.session_state.pop("proyecto_nombre", None)
            st.rerun()

    st.markdown("---")
    st.subheader("Nuevo proyecto")
    with st.form("nuevo_proyecto"):
        nombre_p = st.text_input("Nombre (único)")
        sector_p = st.text_input("Sector")
        tamano_p = st.text_input("Tamaño")
        descripcion_p = st.text_area("Descripción")
        if st.form_submit_button("Crear proyecto"):
            if not nombre_p:
                st.error("El nombre es obligatorio.")
            elif nombre_p in nombres_proyecto:
                st.error(f"Ya existe un proyecto llamado {nombre_p}.")
            else:
                sb.table("proyectos").insert({
                    "nombre": nombre_p, "sector": sector_p or None,
                    "tamano": tamano_p or None, "descripcion": descripcion_p or None,
                }).execute()
                st.success(f"Proyecto {nombre_p} creado.")
                st.session_state["proyecto_nombre"] = nombre_p
                st.rerun()


# ---------------------------------------------------------------------
# Página: Activos
# ---------------------------------------------------------------------
elif pagina == "Activos":
    st.title("Activos")

    # --- Activos existentes (primero, agrupados por tipo, con edición en bloque) ---
    st.subheader(f"Activos existentes ({len(activos)})")

    tabs = st.tabs([f"{t} ({sum(1 for a in activos.values() if a['tipo']==t)})" for t in TIPOS_MAGERIT])

    for tipo_tab, tab in zip(TIPOS_MAGERIT, tabs):
        with tab:
            activos_tipo = [a for a in activos.values() if a["tipo"] == tipo_tab]
            if not activos_tipo:
                st.caption("No hay activos de este tipo.")
                continue

            reset_ctr = st.session_state.setdefault(f"reset_ctr_{tipo_tab}", 0)

            filas_tipo = []
            for a in activos_tipo:
                vp = a["valor_propio"]
                filas_tipo.append({
                    "Código": a["codigo"], "Nombre": a["nombre"], "Subtipo": a["subtipo"],
                    "D": NUMERO_A_VALORACION.get(vp["D"], vp["D"]), "I": NUMERO_A_VALORACION.get(vp["I"], vp["I"]),
                    "C": NUMERO_A_VALORACION.get(vp["C"], vp["C"]), "A": NUMERO_A_VALORACION.get(vp["A"], vp["A"]),
                    "T": NUMERO_A_VALORACION.get(vp["T"], vp["T"]),
                })
            df_tipo = pd.DataFrame(filas_tipo)

            seleccionar_todos = st.checkbox(
                "Seleccionar todos", key=f"select_all_{tipo_tab}_{reset_ctr}",
                help="Aplica a todos los activos de esta pestaña, sin tener que marcarlos uno a uno en la tabla.",
            )

            evento_tabla = st.dataframe(
                df_tipo, use_container_width=True, hide_index=True,
                on_select="rerun", selection_mode="multi-row", key=f"tabla_activos_{tipo_tab}_{reset_ctr}",
            )
            if seleccionar_todos:
                codigos_sel = [a["codigo"] for a in activos_tipo]
            else:
                filas_sel = evento_tabla.selection.rows if evento_tabla and evento_tabla.selection else []
                codigos_sel = [df_tipo.iloc[i]["Código"] for i in filas_sel]

            if len(codigos_sel) == 1:
                st.session_state["cod_editar_prefill"] = codigos_sel[0]

            with st.expander(f"✏️ Aplicar valoración en bloque ({len(codigos_sel)} activo(s) seleccionado(s))",
                              expanded=bool(codigos_sel)):
                if not codigos_sel:
                    st.caption("Selecciona una o varias filas de la tabla (o marca \"Seleccionar todos\") "
                               "para poder aplicarles un valor.")
                else:
                    st.caption("Seleccionados: " + ", ".join(codigos_sel))
                    dims_sel = st.multiselect(
                        "Dimensiones a las que aplicar", DIMENSIONES, default=[], key=f"dims_bloque_{tipo_tab}_{reset_ctr}",
                    )
                    valor_bloque = st.selectbox(
                        "Valor a aplicar", VALORACION_OPCIONES, key=f"valor_bloque_{tipo_tab}_{reset_ctr}",
                    )
                    if st.button(f"Aplicar a {len(codigos_sel)} activo(s)", key=f"btn_aplicar_bloque_{tipo_tab}_{reset_ctr}"):
                        if not dims_sel:
                            st.error("Elige al menos una dimensión.")
                        else:
                            campo_por_dim = {"D": "valor_propio_d", "I": "valor_propio_i",
                                              "C": "valor_propio_c", "A": "valor_propio_a", "T": "valor_propio_t"}
                            actualizacion = {campo_por_dim[d]: VALORACION_A_NUMERO[valor_bloque] for d in dims_sel}
                            ids_sel = [activos[c]["id"] for c in codigos_sel]
                            sb.table("activos").update(actualizacion).in_("id", ids_sel).execute()
                            st.success(
                                f"{', '.join(dims_sel)} = {valor_bloque} aplicado a {len(codigos_sel)} activo(s)."
                            )
                            # Nueva key -> la tabla y las casillas de esta pestaña nacen "limpias"
                            st.session_state[f"reset_ctr_{tipo_tab}"] = reset_ctr + 1
                            st.rerun()

    st.markdown("---")

    # --- Edición / eliminación de activo existente ---
    st.subheader("Editar o eliminar activo")
    if not opciones_codigo:
        st.info("Todavía no hay activos dados de alta.")
    else:
        prefill = st.session_state.get("cod_editar_prefill")
        indice_defecto = opciones_codigo.index(prefill) if prefill in opciones_codigo else 0
        cod_editar = st.selectbox(
            "Selecciona activo", opciones_codigo, index=indice_defecto,
            format_func=lambda c: f"{c} · {activos[c]['nombre']}", key=f"cod_editar_{prefill or 'ninguno'}",
        )

        bcol1, bcol2 = st.columns(2)
        if bcol1.button("✏️ Editar este activo", key="btn_accion_editar", use_container_width=True):
            st.session_state["accion_activo"] = "editar"
            st.session_state["accion_activo_cod"] = cod_editar
        if bcol2.button("🗑️ Eliminar este activo", key="btn_accion_eliminar", use_container_width=True):
            st.session_state["accion_activo"] = "eliminar"
            st.session_state["accion_activo_cod"] = cod_editar

        accion = st.session_state.get("accion_activo")
        accion_cod = st.session_state.get("accion_activo_cod")

        if accion and accion_cod == cod_editar:
            activo_actual = activos[cod_editar]

            if accion == "editar":
                st.markdown(f"#### Editando {cod_editar} · {activo_actual['nombre']}")

                tipo_editar = st.selectbox(
                    "Tipo Magerit", TIPOS_MAGERIT,
                    index=TIPOS_MAGERIT.index(activo_actual["tipo"]), key=f"tipo_editar_activo_{cod_editar}",
                )
                subtipos_disp_editar = subtipos_por_tipo.get(tipo_editar, [])
                subtipos_actuales = [s.strip().upper() for s in (activo_actual["subtipo"] or "").split(";") if s.strip()]
                subtipos_default = [s for s in subtipos_actuales if s in subtipos_disp_editar]
                subtipos_perdidos = [s for s in subtipos_actuales if s not in subtipos_disp_editar]
                if subtipos_perdidos and tipo_editar == activo_actual["tipo"]:
                    st.warning(
                        f"Estos subtipos del activo no están en el catálogo para {tipo_editar} "
                        f"y se perderán si guardas sin volver a añadirlos: {', '.join(subtipos_perdidos)}"
                    )

                with st.expander(f"➕ Añadir subtipo nuevo al catálogo (para {tipo_editar})"):
                    csub1e, csub2e = st.columns([3, 1])
                    nuevo_subtipo_e = csub1e.text_input("Nuevo subtipo", key=f"nuevo_subtipo_editar_txt_{cod_editar}")
                    if csub2e.button("Añadir al catálogo", key=f"btn_add_subtipo_editar_{cod_editar}"):
                        if nuevo_subtipo_e.strip():
                            try:
                                anadir_subtipo_catalogo(nuevo_subtipo_e, tipo_editar)
                                st.success(f"Subtipo {nuevo_subtipo_e.strip().upper()} añadido a {tipo_editar}.")
                                st.rerun()
                            except Exception as e:
                                st.error(f"No se pudo añadir (¿ya existe para este tipo?): {e}")
                        else:
                            st.warning("Escribe un nombre de subtipo antes de añadir.")

                with st.form(f"editar_activo_{cod_editar}"):
                    nombre_e = st.text_input("Nombre", value=activo_actual["nombre"], key=f"nombre_e_{cod_editar}")
                    subtipos_sel_e = st.multiselect("Subtipos", subtipos_disp_editar, default=subtipos_default,
                                                     key=f"subtipos_sel_e_{cod_editar}")

                    st.caption("Valoración propia")
                    vc1e, vc2e, vc3e, vc4e, vc5e = st.columns(5)
                    vp = activo_actual["valor_propio"]
                    val_d_e = vc1e.selectbox("D", VALORACION_OPCIONES, index=idx_valoracion(vp["D"]), key=f"val_d_e_{cod_editar}")
                    val_i_e = vc2e.selectbox("I", VALORACION_OPCIONES, index=idx_valoracion(vp["I"]), key=f"val_i_e_{cod_editar}")
                    val_c_e = vc3e.selectbox("C", VALORACION_OPCIONES, index=idx_valoracion(vp["C"]), key=f"val_c_e_{cod_editar}")
                    val_a_e = vc4e.selectbox("A", VALORACION_OPCIONES, index=idx_valoracion(vp["A"]), key=f"val_a_e_{cod_editar}")
                    val_t_e = vc5e.selectbox("T", VALORACION_OPCIONES, index=idx_valoracion(vp["T"]), key=f"val_t_e_{cod_editar}")

                    if st.form_submit_button("Guardar cambios"):
                        sb.table("activos").update({
                            "nombre": nombre_e, "tipo": tipo_editar,
                            "valor_propio_d": VALORACION_A_NUMERO[val_d_e],
                            "valor_propio_i": VALORACION_A_NUMERO[val_i_e],
                            "valor_propio_c": VALORACION_A_NUMERO[val_c_e],
                            "valor_propio_a": VALORACION_A_NUMERO[val_a_e],
                            "valor_propio_t": VALORACION_A_NUMERO[val_t_e],
                        }).eq("id", activo_actual["id"]).execute()
                        sb.table("activo_subtipos").delete().eq("activo_id", activo_actual["id"]).execute()
                        for s in subtipos_sel_e:
                            sb.table("activo_subtipos").insert({"activo_id": activo_actual["id"], "subtipo": s}).execute()
                        st.success(f"Activo {cod_editar} actualizado.")
                        del st.session_state["accion_activo"]
                        st.rerun()

            elif accion == "eliminar":
                st.markdown(f"#### Eliminar {cod_editar} · {activo_actual['nombre']}")
                n_dep = sb.table("dependencias").select("id", count="exact") \
                    .or_(f"activo_superior_id.eq.{activo_actual['id']},activo_inferior_id.eq.{activo_actual['id']}") \
                    .execute().count or 0
                n_per = sb.table("personas_asociadas").select("id", count="exact") \
                    .eq("activo_id", activo_actual["id"]).execute().count or 0
                if n_dep or n_per:
                    st.warning(
                        f"Este activo tiene {n_dep} dependencia(s) y {n_per} persona(s) asociada(s) "
                        "que se eliminarán también al borrarlo."
                    )
                confirmar_borrado = st.checkbox(
                    f"Sí, entiendo que se borrará {cod_editar} y sus relaciones asociadas",
                    key="confirmar_borrado_activo",
                )
                if st.button("🗑️ Eliminar activo", disabled=not confirmar_borrado):
                    sb.table("activos").delete().eq("id", activo_actual["id"]).execute()
                    st.success(f"Activo {cod_editar} eliminado.")
                    del st.session_state["accion_activo"]
                    st.rerun()

    st.markdown("---")

    # --- Alta de nuevo activo ---
    st.subheader("Nuevo activo")
    tipo_nuevo = st.selectbox("Tipo Magerit", TIPOS_MAGERIT, key="tipo_nuevo_activo")
    subtipos_disp_nuevo = subtipos_por_tipo.get(tipo_nuevo, [])

    with st.expander(f"➕ Añadir subtipo nuevo al catálogo (para {tipo_nuevo})"):
        csub1, csub2 = st.columns([3, 1])
        nuevo_subtipo_txt = csub1.text_input("Nuevo subtipo", key="nuevo_subtipo_txt")
        if csub2.button("Añadir al catálogo", key="btn_add_subtipo"):
            if nuevo_subtipo_txt.strip():
                try:
                    anadir_subtipo_catalogo(nuevo_subtipo_txt, tipo_nuevo)
                    st.success(f"Subtipo {nuevo_subtipo_txt.strip().upper()} añadido a {tipo_nuevo}.")
                    st.rerun()
                except Exception as e:
                    st.error(f"No se pudo añadir (¿ya existe para este tipo?): {e}")
            else:
                st.warning("Escribe un nombre de subtipo antes de añadir.")

    with st.form("nuevo_activo"):
        c1, c2 = st.columns(2)
        codigo = c1.text_input("Código (único)")
        nombre = c2.text_input("Nombre")
        subtipos_sel = st.multiselect(
            "Subtipos", subtipos_disp_nuevo,
            help="Si el subtipo que necesitas no aparece, añádelo primero arriba al catálogo.",
        )

        st.caption("Valoración propia — deja 'n.a' si esa dimensión no aplica a este tipo de activo")
        vc1, vc2, vc3, vc4, vc5 = st.columns(5)
        val_d = vc1.selectbox("D", VALORACION_OPCIONES, index=0)
        val_i = vc2.selectbox("I", VALORACION_OPCIONES, index=0)
        val_c = vc3.selectbox("C", VALORACION_OPCIONES, index=0)
        val_a = vc4.selectbox("A", VALORACION_OPCIONES, index=0)
        val_t = vc5.selectbox("T", VALORACION_OPCIONES, index=0)

        if st.form_submit_button("Guardar activo"):
            if not codigo or not nombre:
                st.error("Código y Nombre son obligatorios.")
            elif codigo in activos:
                st.error(f"Ya existe un activo con código {codigo}.")
            else:
                nuevo = sb.table("activos").insert({
                    "proyecto_id": st.session_state["proyecto_id"],
                    "codigo": codigo, "nombre": nombre, "tipo": tipo_nuevo,
                    "valor_propio_d": VALORACION_A_NUMERO[val_d],
                    "valor_propio_i": VALORACION_A_NUMERO[val_i],
                    "valor_propio_c": VALORACION_A_NUMERO[val_c],
                    "valor_propio_a": VALORACION_A_NUMERO[val_a],
                    "valor_propio_t": VALORACION_A_NUMERO[val_t],
                }).execute()
                activo_id = nuevo.data[0]["id"]
                for s in subtipos_sel:
                    sb.table("activo_subtipos").insert({"activo_id": activo_id, "subtipo": s}).execute()
                st.success(f"Activo {codigo} creado.")
                st.rerun()


# ---------------------------------------------------------------------
# Página: Dependencias
# ---------------------------------------------------------------------
elif pagina == "Dependencias":
    st.title("Dependencias")
    st.caption("Regla Magerit: la Información siempre es Activo Superior respecto al Servicio "
               "que la gestiona (nunca al revés). No encadenar: cada activo inferior cuelga "
               "directamente del superior del que depende de verdad.")

    dep_edges, dep_raw = cargar_dependencias(id_to_codigo)
    dep_por_id = {r["id"]: r for r in dep_raw}
    dep_adj_forward = construir_adj_forward(dep_edges)

    if not opciones_codigo:
        st.info("Todavía no hay activos dados de alta.")
    else:
        cod_foco = st.selectbox(
            "Selecciona un activo", opciones_codigo,
            format_func=lambda c: f"{c} · {activos[c]['nombre']}", key="dep_foco",
        )
        activo_id_foco = activos[cod_foco]["id"]

        st.subheader(f"Dependencias de {cod_foco}")
        col_sup, col_inf = st.columns(2)

        with col_sup:
            st.markdown("**Depende de (activos superiores)**")
            superiores = [(r["id"], id_to_codigo[r["activo_superior_id"]], r["grado"])
                          for r in dep_raw if r["activo_inferior_id"] == activo_id_foco]
            if not superiores:
                st.caption("— ninguna —")
            for dep_id, cod_sup_r, grado_r in superiores:
                cc1, cc2 = st.columns([5, 1.3])
                cc1.write(f"{cod_sup_r} · {activos[cod_sup_r]['nombre']} — {grado_r}%")
                with cc2:
                    ccb1, ccb2 = st.columns(2)
                    if ccb1.button("✏️", key=f"edit_sup_{dep_id}", help="Editar esta dependencia"):
                        st.session_state["dep_editando"] = dep_id
                        st.rerun()
                    if ccb2.button("🗑️", key=f"del_sup_{dep_id}", help="Eliminar esta dependencia"):
                        sb.table("dependencias").delete().eq("id", dep_id).execute()
                        st.rerun()

        with col_inf:
            st.markdown("**Del que dependen (activos inferiores)**")
            inferiores = [(r["id"], id_to_codigo[r["activo_inferior_id"]], r["grado"])
                          for r in dep_raw if r["activo_superior_id"] == activo_id_foco]
            if not inferiores:
                st.caption("— ninguna —")
            for dep_id, cod_inf_r, grado_r in inferiores:
                cc1, cc2 = st.columns([5, 1.3])
                cc1.write(f"{cod_inf_r} · {activos[cod_inf_r]['nombre']} — {grado_r}%")
                with cc2:
                    ccb1, ccb2 = st.columns(2)
                    if ccb1.button("✏️", key=f"edit_inf_{dep_id}", help="Editar esta dependencia"):
                        st.session_state["dep_editando"] = dep_id
                        st.rerun()
                    if ccb2.button("🗑️", key=f"del_inf_{dep_id}", help="Eliminar esta dependencia"):
                        sb.table("dependencias").delete().eq("id", dep_id).execute()
                        st.rerun()

        st.subheader("Árbol de dependencias (descendente)")
        trans = dependencias_transitivas(cod_foco, dep_adj_forward)
        if not trans:
            st.caption("Este activo no tiene dependencias descendentes.")
        else:
            nodos_incluidos = {cod_foco} | set(trans.keys())
            dot = ['digraph G {', 'rankdir=LR;', 'node [shape=box, style=filled, fontsize=10, fontname="Helvetica"];']
            for n in nodos_incluidos:
                color = "#c7d2fe" if n == cod_foco else "#eef2ff"
                nombre_n = activos[n]["nombre"].replace('"', "'")
                dot.append(f'"{n}" [label="{n}\\n{nombre_n}", fillcolor="{color}"];')
            for sup, inf, grado in dep_edges:
                if sup in nodos_incluidos and inf in nodos_incluidos:
                    dot.append(f'"{sup}" -> "{inf}" [label="{grado}%", fontsize=9];')
            dot.append('}')
            st.graphviz_chart("\n".join(dot))

    st.markdown("---")

    dep_editando_id = st.session_state.get("dep_editando")
    dep_editando = dep_por_id.get(dep_editando_id) if dep_editando_id else None

    if dep_editando:
        st.subheader("Editar dependencia")
        if st.button("✖ Cancelar edición", key="cancelar_edicion_dep"):
            del st.session_state["dep_editando"]
            st.rerun()
        default_sup = id_to_codigo[dep_editando["activo_superior_id"]]
        default_inf = id_to_codigo[dep_editando["activo_inferior_id"]]
        default_grado = dep_editando["grado"]
        default_just = dep_editando.get("justificacion") or ""
        identificador = f"dep_{dep_editando_id}"
        form_key = f"editar_dependencia_{dep_editando_id}"
    else:
        st.subheader("Nueva dependencia")
        # El activo que estás mirando arriba se precarga como Activo Superior.
        default_sup = cod_foco if opciones_codigo else None
        default_inf = None
        default_grado = 100
        default_just = ""
        identificador = f"nuevo_{cod_foco}" if opciones_codigo else "nuevo"
        form_key = f"nueva_dependencia_{cod_foco}" if opciones_codigo else "nueva_dependencia"

    with st.form(form_key):
        c1, c2, c3 = st.columns(3)
        cod_sup = c1.selectbox(
            "Activo Superior", opciones_codigo,
            index=opciones_codigo.index(default_sup) if default_sup in opciones_codigo else 0,
            format_func=lambda c: f"{c} · {activos[c]['nombre']}", key=f"cod_sup_{identificador}",
        )
        cod_inf = c2.selectbox(
            "Activo Inferior", opciones_codigo,
            index=opciones_codigo.index(default_inf) if default_inf in opciones_codigo else 0,
            format_func=lambda c: f"{c} · {activos[c]['nombre']}", key=f"cod_inf_{identificador}",
        )
        grado = c3.slider("Grado (%)", 0, 100, int(default_grado), key=f"grado_{identificador}")
        justificacion = st.text_area("Justificación", value=default_just, key=f"just_{identificador}")

        etiqueta_boton = "Guardar cambios" if dep_editando else "Guardar dependencia"
        if st.form_submit_button(etiqueta_boton):
            if cod_sup == cod_inf:
                st.error("Un activo no puede depender de sí mismo.")
            else:
                datos = {
                    "activo_superior_id": activos[cod_sup]["id"],
                    "activo_inferior_id": activos[cod_inf]["id"],
                    "grado": grado, "justificacion": justificacion or None,
                }
                if dep_editando:
                    sb.table("dependencias").update(datos).eq("id", dep_editando_id).execute()
                    st.success(f"Dependencia {cod_sup} → {cod_inf} ({grado}%) actualizada.")
                    del st.session_state["dep_editando"]
                else:
                    sb.table("dependencias").insert(datos).execute()
                    st.success(f"Dependencia {cod_sup} → {cod_inf} ({grado}%) creada.")
                st.rerun()


# ---------------------------------------------------------------------
# Página: Personas asociadas
# ---------------------------------------------------------------------
elif pagina == "Personas asociadas":
    st.title("Personas asociadas")
    st.caption("El nombre es descriptivo — las amenazas se calculan según el Tipo de rol, no según el nombre.")

    with st.form("nueva_persona"):
        c1, c2, c3, c4 = st.columns(4)
        cod_act = c1.selectbox("Activo", opciones_codigo,
                                format_func=lambda c: f"{c} · {activos[c]['nombre']}")
        persona_nombre = c2.text_input("Persona / Rol (descriptivo)")
        tipo_rol = c3.selectbox("Tipo de rol", ROLES)
        grado = c4.slider("Grado (%)", 0, 100, 100)
        justificacion = st.text_area("Justificación", key="just_persona")

        if st.form_submit_button("Guardar persona asociada"):
            if not persona_nombre:
                st.error("El campo Persona / Rol es obligatorio (aunque no se use para buscar amenazas).")
            else:
                sb.table("personas_asociadas").insert({
                    "activo_id": activos[cod_act]["id"], "persona_nombre": persona_nombre,
                    "tipo_rol": tipo_rol, "grado": grado, "justificacion": justificacion or None,
                }).execute()
                st.success(f"Persona asociada a {cod_act} creada.")
                st.rerun()

    per_edges, per_raw = cargar_personas(id_to_codigo)
    st.subheader(f"Personas asociadas existentes ({len(per_edges)})")
    if not per_raw:
        st.caption("— ninguna —")
    for r in per_raw:
        cod = id_to_codigo[r["activo_id"]]
        c1, c2, c3, c4 = st.columns([2, 3, 2, 1])
        c1.write(f"{cod} · {activos[cod]['nombre']}")
        c2.write(r["persona_nombre"])
        c3.write(f"{r['tipo_rol']} — {r['grado']}%")
        if c4.button("🗑️", key=f"del_persona_{r['id']}"):
            sb.table("personas_asociadas").delete().eq("id", r["id"]).execute()
            st.success("Asociación eliminada.")
            st.rerun()


# ---------------------------------------------------------------------
# Página: Salvaguardas (catálogo + categorías que protege + activos donde
# está desplegada) -- Riesgo Residual, ver Metodologia_App_Riesgos.md
# ---------------------------------------------------------------------
elif pagina == "Salvaguardas":
    st.title("Salvaguardas")
    st.caption("Cada salvaguarda protege una o varias categorías de amenaza, y se asigna "
               "a los activos donde está desplegada de verdad. La protección se propaga "
               "automáticamente a los activos que dependen de ese activo.")

    TIPOS_SALVAGUARDA = ["PR", "DR", "EL", "IM", "CR", "RC"]
    NOMBRE_TIPO_SALVAGUARDA = {
        "PR": "Preventiva", "DR": "Disuasoria", "EL": "Eliminatoria",
        "IM": "Minimizadora", "CR": "Correctiva", "RC": "Recuperativa",
    }

    salvaguardas, categorias_por_salvaguarda, asignaciones = cargar_salvaguardas()
    res_cats = sb.table("categorias_amenaza").select("id, nombre").order("nombre").execute()
    categorias_todas = {r["id"]: r["nombre"] for r in res_cats.data}
    nombre_a_id_categoria = {v: k for k, v in categorias_todas.items()}
    nombres_categoria_ordenados = sorted(categorias_todas.values())
    tipos_por_categoria = cargar_tipos_por_categoria()

    st.subheader(f"Salvaguardas existentes ({len(salvaguardas)})")
    if not salvaguardas:
        st.caption("— ninguna —")
    for sid, s in sorted(salvaguardas.items(), key=lambda kv: clave_orden_804(kv[1]["nombre"])):
        etiqueta = (f"{s['nombre']} ({NOMBRE_TIPO_SALVAGUARDA.get(s['tipo'], s['tipo'])}) — "
                    f"ei={s['eficacia_impacto']}% · ep={s['eficacia_probabilidad']}%")
        with st.expander(etiqueta):
            if s.get("descripcion"):
                st.caption(s["descripcion"])

            cats_actuales = categorias_por_salvaguarda.get(sid, [])
            nuevas_cats = st.multiselect(
                "Categorías de amenaza que protege", nombres_categoria_ordenados,
                default=cats_actuales, key=f"cats_{sid}",
            )
            if st.button("Guardar categorías", key=f"guardar_cats_{sid}"):
                sb.table("salvaguarda_categoria").delete().eq("salvaguarda_id", sid).execute()
                for cat_nombre in nuevas_cats:
                    sb.table("salvaguarda_categoria").insert({
                        "salvaguarda_id": sid, "categoria_id": nombre_a_id_categoria[cat_nombre],
                    }).execute()
                st.success("Categorías actualizadas.")
                st.rerun()

            familia_catalogo = s.get("familia") or "Manual"

            st.markdown("**Activos donde está desplegada**")
            asignadas_aqui = [a for a in asignaciones
                               if a["salvaguarda_id"] == sid and a["activo_id"] in id_to_codigo]
            if not asignadas_aqui:
                st.caption("— sin asignar a ningún activo —")

            for asig in asignadas_aqui:
                activo_id = asig["activo_id"]
                cod = id_to_codigo[activo_id]
                clave = f"{sid}_{activo_id}"

                with st.container(border=True):
                    ca1, ca_del = st.columns([5, 1])
                    ca1.markdown(f"**{cod} · {activos[cod]['nombre']}**")
                    if ca_del.button("🗑️", key=f"del_asig_{clave}"):
                        sb.table("activo_salvaguardas").delete() \
                            .eq("activo_id", activo_id).eq("salvaguarda_id", sid).execute()
                        st.rerun()

                    cb1, cb2 = st.columns([1, 2])
                    usar_familia_propia = cb1.checkbox(
                        "Sé cómo se implanta aquí", value=asig["familia_override"] is not None,
                        key=f"chk_fam_{clave}",
                    )
                    if usar_familia_propia:
                        familias_lista = list(TABLAS_FAMILIA.keys())
                        idx_familia = familias_lista.index(asig["familia_override"] or familia_catalogo)
                        familia_efectiva = cb2.selectbox(
                            "Familia", familias_lista, index=idx_familia,
                            format_func=lambda f: NOMBRE_FAMILIA[f],
                            key=f"fam_{clave}", label_visibility="collapsed",
                        )
                    else:
                        familia_efectiva = familia_catalogo
                        cb2.caption(f"Familia (catálogo): {NOMBRE_FAMILIA[familia_catalogo]} — "
                                    "marca la casilla para indicar otra si la conoces")

                    nivel_actual = int(asig["nivel_madurez"])
                    nivel_nuevo = st.selectbox(
                        "Nivel de madurez", list(range(6)), index=nivel_actual,
                        format_func=lambda n: NOMBRE_NIVEL_MADUREZ[n],
                        key=f"niv_{clave}",
                    )

                    # Si cambia nivel o familia respecto a lo guardado, el valor
                    # por defecto de esa combinación sustituye a Coverage/Reliability
                    # -- si no ha cambiado nada, se respeta lo que ya hay guardado
                    # (que puede ser un valor editado a mano por el analista).
                    cambio_contexto = (nivel_nuevo != nivel_actual) or \
                        (usar_familia_propia and familia_efectiva != (asig["familia_override"] or familia_catalogo)) or \
                        (not usar_familia_propia and asig["familia_override"] is not None)
                    if cambio_contexto:
                        cov_mostrar, rel_mostrar = TABLAS_FAMILIA[familia_efectiva][nivel_nuevo]
                    else:
                        cov_mostrar, rel_mostrar = asig["coverage"], asig["reliability"]

                    cc1, cc2 = st.columns(2)
                    coverage_nuevo = cc1.number_input(
                        "Coverage (%)", min_value=0, max_value=100, value=int(cov_mostrar),
                        key=f"cov_{clave}_{nivel_nuevo}_{familia_efectiva}",
                    )
                    reliability_nuevo = cc2.number_input(
                        "Reliability (%)", min_value=0, max_value=100, value=int(rel_mostrar),
                        key=f"rel_{clave}_{nivel_nuevo}_{familia_efectiva}",
                    )
                    ei_ef = (s["eficacia_impacto"] or 0) * coverage_nuevo / 100 * reliability_nuevo / 100
                    ep_ef = (s["eficacia_probabilidad"] or 0) * coverage_nuevo / 100 * reliability_nuevo / 100
                    st.caption(f"Eficacia efectiva: ei={ei_ef:.1f}% · ep={ep_ef:.1f}%")

                    if st.button("💾 Guardar", key=f"guardar_{clave}"):
                        sb.table("activo_salvaguardas").update({
                            "familia_override": familia_efectiva if usar_familia_propia else None,
                            "nivel_madurez": nivel_nuevo,
                            "coverage": coverage_nuevo, "reliability": reliability_nuevo,
                        }).eq("activo_id", activo_id).eq("salvaguarda_id", sid).execute()
                        st.rerun()

            codigos_ya_asignados = {id_to_codigo[a["activo_id"]] for a in asignadas_aqui}

            # Filtro: solo activos de un tipo al que llegue alguna amenaza
            # de las categorias que protege esta salvaguarda. Si la
            # salvaguarda no tiene categorias vinculadas, o ninguna
            # categoria suya tiene tipos conocidos, no se filtra nada
            # (se muestran todos) para no ocultar activos por falta de dato.
            categorias_esta = categorias_por_salvaguarda.get(sid, [])
            tipos_aplicables = set()
            for cat in categorias_esta:
                tipos_aplicables |= tipos_por_categoria.get(cat, set())

            if tipos_aplicables:
                disponibles = [c for c in opciones_codigo
                                if c not in codigos_ya_asignados and activos[c]["tipo"] in tipos_aplicables]
                ocultos = [c for c in opciones_codigo
                           if c not in codigos_ya_asignados and activos[c]["tipo"] not in tipos_aplicables]
                if ocultos:
                    st.caption(f"Mostrando solo activos de tipo {', '.join(sorted(tipos_aplicables))} "
                               f"({len(ocultos)} activo(s) de otro tipo ocultos, no reciben esta categoría de amenaza).")
            else:
                disponibles = [c for c in opciones_codigo if c not in codigos_ya_asignados]

            if disponibles:
                na1, na2 = st.columns([4, 1])
                nuevo_activo = na1.selectbox(
                    "Añadir a otro activo", disponibles,
                    format_func=lambda c: f"{c} · {activos[c]['nombre']}",
                    key=f"nuevo_activo_{sid}", label_visibility="collapsed",
                )
                if na2.button("➕", key=f"add_asig_{sid}"):
                    cov_ini, rel_ini = TABLAS_FAMILIA[familia_catalogo][5]
                    sb.table("activo_salvaguardas").insert({
                        "activo_id": activos[nuevo_activo]["id"], "salvaguarda_id": sid,
                        "nivel_madurez": 5, "coverage": cov_ini, "reliability": rel_ini,
                    }).execute()
                    st.rerun()

            st.markdown("---")
            if st.button("🗑️ Eliminar esta salvaguarda", key=f"del_salv_{sid}"):
                sb.table("salvaguardas").delete().eq("id", sid).execute()
                st.success(f"'{s['nombre']}' eliminada.")
                st.rerun()

    st.markdown("---")
    st.subheader("Nueva salvaguarda")
    with st.form("nueva_salvaguarda"):
        nombre_s = st.text_input("Nombre")
        tipo_s = st.selectbox("Tipo", TIPOS_SALVAGUARDA,
                               format_func=lambda t: f"{t} — {NOMBRE_TIPO_SALVAGUARDA[t]}")
        familia_s = st.selectbox(
            "Familia de implantación (por defecto para nuevas asignaciones)",
            list(TABLAS_FAMILIA.keys()), index=2, format_func=lambda f: NOMBRE_FAMILIA[f],
        )
        c1, c2 = st.columns(2)
        ei_s = c1.slider("Eficacia frente a Impacto (%)", 0, 100, 0)
        ep_s = c2.slider("Eficacia frente a Probabilidad (%)", 0, 100, 0)
        descripcion_s = st.text_area("Descripción (opcional)")

        if st.form_submit_button("Guardar salvaguarda"):
            if not nombre_s:
                st.error("El nombre es obligatorio.")
            else:
                sb.table("salvaguardas").insert({
                    "nombre": nombre_s, "tipo": tipo_s, "familia": familia_s,
                    "eficacia_impacto": ei_s, "eficacia_probabilidad": ep_s,
                    "descripcion": descripcion_s or None,
                }).execute()
                st.success(f"Salvaguarda '{nombre_s}' creada.")
                st.rerun()



# ---------------------------------------------------------------------
# Página: Amenazas (directas + heredadas, por activo)
# ---------------------------------------------------------------------
elif pagina == "Amenazas":
    st.title("Amenazas")

    if st.button("🔄 Recalcular", type="primary", key="recalc_amenazas"):
        recalcular_todo(activos, id_to_codigo)

    if "filas_prop" not in st.session_state:
        st.info("Pulsa Recalcular para ver los resultados.")
    else:
        cod_sel = st.selectbox(
            "Activo", opciones_codigo,
            format_func=lambda c: f"{c} · {activos[c]['nombre']}", key="amenazas_activo",
        )

        st.subheader("Asignación directa")
        directas = st.session_state["amenazas_directas"].get(cod_sel, [])
        filas_d = [{"Amenaza": am["amenaza"], "Categoría": am.get("categoria"),
                    "Probabilidad": am["probabilidad"], **{d: am.get(d) for d in DIMENSIONES}}
                   for am in directas]
        if filas_d:
            st.dataframe(pd.DataFrame(filas_d), use_container_width=True, hide_index=True)
        else:
            st.caption("Sin amenazas directas asignadas.")

        st.subheader("Heredadas (por dependencia o persona asociada)")
        heredadas = [f for f in st.session_state["filas_prop"]
                     if f["codigo"] == cod_sel and f["origen"] != "Directa"]
        filas_h = [{"Origen": f["origen"], "Procedencia": f["detalle_origen"], "Amenaza": f["amenaza"],
                    "Probabilidad": f["probabilidad"], "Grado transitivo": f"{f['grado_transitivo']}%",
                    **{d: f.get(d) for d in DIMENSIONES}} for f in heredadas]
        if filas_h:
            st.dataframe(pd.DataFrame(filas_h), use_container_width=True, hide_index=True)
        else:
            st.caption("Sin amenazas heredadas.")


# ---------------------------------------------------------------------
# Página: Impacto (vista resumen tipo PILAR)
# ---------------------------------------------------------------------
elif pagina == "Impacto":
    render_pagina_metrica(
        "Impacto", "impacto_cualitativo", "impacto",
        subtitulo="La magnitud del daño si la amenaza ocurriera, sin tener en cuenta lo probable que sea.",
    )

elif pagina == "Riesgo":
    render_pagina_metrica(
        "Riesgo", "riesgo_cualitativo", "riesgo",
        subtitulo="Impacto ponderado por la probabilidad de que la amenaza ocurra — combina gravedad y frecuencia.",
    )
