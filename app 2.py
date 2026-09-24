"""
app.py — Modelo de riesgo TRC (Magerit 3.0)

Aplicación Streamlit para:
  - gestionar varios proyectos de análisis independientes (cada uno con
    sus propios activos, dependencias y personas asociadas; el catálogo
    de amenazas es compartido por todos),
  - dar de alta, editar y eliminar activos y su valoración (subtipos
    seleccionables de un catálogo controlado, no texto libre),
  - definir y eliminar dependencias, con vista por activo y árbol de
    dependencias descendente,
  - definir personas asociadas,
  - recalcular y consultar amenazas (directas y heredadas) e impacto
    (vista resumen por activo con detalle al seleccionar).

Requiere en .streamlit/secrets.toml (o variables de entorno equivalentes):
    SUPABASE_URL = "https://xxxx.supabase.co"
    SUPABASE_SERVICE_ROLE_KEY = "..."   # la "service_role" key — NUNCA la "anon"
    # RLS está activo en todas las tablas SIN políticas para "anon":
    # esta app solo funciona con service_role, que ignora RLS por diseño.
    # Trata esta clave como una contraseña de administrador: solo en
    # secrets.toml / "Secrets" de Streamlit Cloud, nunca en el navegador
    # del usuario ni en un repositorio público.

Requiere también haber ejecutado migracion_v2_subtipos_catalogo.sql y
migracion_multiproyecto.sql sobre la base de datos (añaden, respectivamente,
la tabla subtipos_catalogo y la tabla proyectos + activos.proyecto_id).
El árbol de dependencias usa st.graphviz_chart, que necesita el binario
`graphviz` instalado en el sistema (ver packages.txt si se despliega en
Streamlit Community Cloud).
"""

from collections import defaultdict

import streamlit as st
import pandas as pd
from supabase import create_client

from calculo import (
    ejecutar_recalculo, construir_adj_forward, dependencias_transitivas,
    impacto_maximo_por_activo, riesgo_maximo_por_activo,
    matriz_por_activo, calcular_valor_acumulado, asignar_amenazas,
    aplicar_ajustes, _excluir_para_destino, DIMENSIONES,
)

st.set_page_config(page_title="Modelo de riesgo TRC", layout="wide")

TIPOS_MAGERIT = ["INFORMACION", "SERVICIO", "SOFTWARE", "EQUIPAMIENTO",
                  "COMUNICACIONES", "INSTALACIONES", "PERSONAL"]
ROLES = ["Usuario", "Operador", "Administrador", "Desarrollador", "Responsable"]
PROBABILIDADES = ["Muy Alta", "Alta", "Media", "Baja", "Muy Baja"]
CAMPO_DEGRADACION = {"D": "degradacion_d", "I": "degradacion_i", "C": "degradacion_c",
                     "A": "degradacion_a", "T": "degradacion_t"}

# st.dialog es estable desde Streamlit 1.37; en versiones anteriores
# existe como st.experimental_dialog.
_dialog = getattr(st, "dialog", None) or getattr(st, "experimental_dialog")
# Escala de Valor de Magerit (Libro II cap. 4) — se muestra siempre por
# nombre de banda, nunca el número (decisión de interfaz; el número
# sigue siendo el que se guarda y calcula por dentro).
VALORACION_OPCIONES = ["n.a", "Despreciable", "Bajo", "Medio", "Alto", "Muy Alto", "Extremo"]
VALORACION_A_NUMERO = {
    "n.a": None, "Despreciable": 0.0, "Bajo": 1.5, "Medio": 4.0,
    "Alto": 7.0, "Muy Alto": 9.0, "Extremo": 10.0,
}
NUMERO_A_VALORACION = {v: k for k, v in VALORACION_A_NUMERO.items()}

# Colores por banda de Valor/Impacto (mismas 6 bandas de la escala de
# Magerit). Se usan para "Ver valor acumulado" en Activos y siempre en
# la página Impacto.
COLOR_VALOR_BANDA = {
    "n.a": "",
    "Despreciable": "background-color: #ffffff",
    "Bajo": "background-color: #fef9c3",
    "Medio": "background-color: #fdba74",
    "Alto": "background-color: #fca5a5",
    "Muy Alto": "background-color: #b91c1c; color: #ffffff",
    "Extremo": "background-color: #581c87; color: #ffffff",
}


def color_valor_banda(nombre_banda):
    return COLOR_VALOR_BANDA.get(nombre_banda, "")


def valor_a_banda(valor_numerico):
    """0-10 -> nombre de banda. valor_acumulado siempre coincide EXACTO
    con uno de los 6 valores de VALORACION_A_NUMERO (es un máximo de
    valores que ya vienen de esa misma escala), así que no hace falta
    redondear a la banda más cercana — solo puede fallar si algún día
    se calcula con datos que no vengan de esta escala."""
    if valor_numerico is None:
        return "n.a"
    exacto = NUMERO_A_VALORACION.get(round(valor_numerico, 1))
    if exacto:
        return exacto
    # Red de seguridad por si algún valor no coincide exacto (no debería
    # ocurrir con valor_acumulado, ver docstring).
    if valor_numerico >= 9.5:
        return "Extremo"
    if valor_numerico >= 8:
        return "Muy Alto"
    if valor_numerico >= 5.5:
        return "Alto"
    if valor_numerico >= 2.75:
        return "Medio"
    if valor_numerico > 0:
        return "Bajo"
    return "Despreciable"


# Grado de dependencia: 5 niveles fijos en vez de un control continuo —
# mismo criterio que Valor/Probabilidad/Degradación, para no aparentar
# una precisión que no es real. No es una escala de Magerit — decisión
# propia de TRC (Magerit deja la dependencia como booleana en el modelo
# cualitativo, o como fracción continua en el cuantitativo).
GRADO_OPCIONES = [
    "Dependencia total — impacto directo",
    "Dependencia total — impacto atenuado",
    "Dependencia relevante, no crítica",
    "Dependencia parcial",
    "Dependencia residual",
]
GRADO_A_NUMERO = {
    "Dependencia total — impacto directo": 100,
    "Dependencia total — impacto atenuado": 75,
    "Dependencia relevante, no crítica": 50,
    "Dependencia parcial": 25,
    "Dependencia residual": 10,
}
NUMERO_A_GRADO = {v: k for k, v in GRADO_A_NUMERO.items()}


def grado_a_banda_mas_cercana(valor_numerico):
    """Para dependencias ya existentes que no coincidan exacto con uno de
    los 5 niveles (datos de antes de este cambio) — corte por el punto
    medio entre niveles consecutivos."""
    if valor_numerico is None:
        return GRADO_OPCIONES[0]
    cortes = [(87.5, 100), (62.5, 75), (37.5, 50), (17.5, 25), (0, 10)]
    for corte, nivel in cortes:
        if valor_numerico >= corte:
            return NUMERO_A_GRADO[nivel]
    return NUMERO_A_GRADO[10]


# Cortes de color para Impacto (banda de Valor) — Bajo=1.5, Medio=4.0,
# Alto=7.0, igual que la escala de Valor de Magerit.
CORTE_BAJO, CORTE_MEDIO, CORTE_ALTO = 1.5, 4.0, 7.0

# Colores del Riesgo cualitativo — escala propia (no de Magerit), 6
# tramos con cortes fijos acordados con TRC.
TRAMOS_RIESGO = [
    (1.0,  "background-color: #d1fae5"),   # < 1: verde claro
    (3.0,  "background-color: #fef9c3"),   # 1 - 2.9: amarillo claro
    (6.0,  "background-color: #fdba74"),   # 3 - 5.9: naranja
    (8.6,  "background-color: #fca5a5"),   # 6 - 8.5: rojo claro
    (9.6,  "background-color: #b91c1c; color: #ffffff"),   # 8.6 - 9.5: rojo oscuro
    (10.01,"background-color: #581c87; color: #ffffff"),   # 9.6 - 10: morado oscuro
]


def color_riesgo(val):
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return ""
    for limite, color in TRAMOS_RIESGO:
        if val < limite:
            return color
    return TRAMOS_RIESGO[-1][1]


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
    """Sólo dependencias cuyo activo superior pertenece al proyecto activo
    (dependencias no tiene proyecto_id propio: se filtra por los activos
    del proyecto, vía id_to_codigo, que ya viene filtrado por proyecto)."""
    ids_proyecto = list(id_to_codigo.keys())
    if not ids_proyecto:
        return [], []
    res = sb.table("dependencias").select("*").in_("activo_superior_id", ids_proyecto).execute()
    return [(id_to_codigo[r["activo_superior_id"]], id_to_codigo[r["activo_inferior_id"]], r["grado"])
            for r in res.data], res.data


def cargar_personas(id_to_codigo):
    """Personas asociadas del proyecto: cada fila vincula un Servicio con
    un activo de tipo Personal (persona_activo_id), siempre al 100%.
    Devuelve ([(codigo_servicio, codigo_persona, rol), ...], filas_crudas).
    Se ignoran filas sin persona_activo_id o cuya persona no pertenezca
    al proyecto."""
    ids_proyecto = list(id_to_codigo.keys())
    if not ids_proyecto:
        return [], []
    res = sb.table("personas_asociadas").select("*").in_("activo_id", ids_proyecto).execute()
    validas = [r for r in res.data if r.get("persona_activo_id") in id_to_codigo]
    return [(id_to_codigo[r["activo_id"]], id_to_codigo[r["persona_activo_id"]], r["tipo_rol"])
            for r in validas], validas


def cargar_ajustes(id_to_codigo):
    """{codigo_activo: {amenaza_id: {"excluida", "probabilidad", "D".."T"}}}
    desde ajustes_amenaza_activo, solo para los activos del proyecto."""
    ids_proyecto = list(id_to_codigo.keys())
    if not ids_proyecto:
        return {}
    res = sb.table("ajustes_amenaza_activo").select("*").in_("activo_id", ids_proyecto).execute()
    ajustes = defaultdict(dict)
    for r in res.data:
        ajustes[id_to_codigo[r["activo_id"]]][r["amenaza_id"]] = {
            "excluida": r["excluida"], "probabilidad": r["probabilidad"],
            **{dim: r[campo] for dim, campo in CAMPO_DEGRADACION.items()},
        }
    return dict(ajustes)


def cargar_catalogo():
    """El catálogo de amenazas es GLOBAL: se comparte entre todos los
    proyectos, por lo que no se filtra por proyecto. Los cambios propios
    de un activo concreto van en ajustes_amenaza_activo."""
    res = sb.table("catalogo_amenazas").select(
        "*, categorias_amenaza(nombre), amenaza_tipo_activo(tipo_activo)"
    ).eq("vigente", True).execute()
    catalogo = []
    for row in res.data:
        catalogo.append({
            "id": row["id"],
            "amenaza": row["amenaza"],
            "categoria": (row.get("categorias_amenaza") or {}).get("nombre"),
            "subcategoria": row["subcategoria"],
            "probabilidad": row["probabilidad"],
            "D": row["degradacion_d"], "I": row["degradacion_i"], "C": row["degradacion_c"],
            "A": row["degradacion_a"], "T": row["degradacion_t"],
            "tipos_activo": [t["tipo_activo"] for t in row.get("amenaza_tipo_activo", [])],
        })
    return catalogo


def cargar_subtipos_catalogo():
    """{tipo_magerit: [subtipo, ...]} ordenado, desde la tabla subtipos_catalogo.
    Normalizado a mayúsculas al leer (no solo al insertar desde la app) —
    para que una fila metida a mano en Supabase en minúsculas no rompa en
    silencio la comparación con activo_subtipos (que sí siempre está en
    mayúsculas)."""
    res = sb.table("subtipos_catalogo").select("*").execute()
    por_tipo = defaultdict(list)
    for r in res.data:
        por_tipo[r["tipo_magerit"]].append(r["subtipo"].strip().upper())
    return {k: sorted(set(v)) for k, v in por_tipo.items()}


def cargar_mapeo_subtipo_subcategoria():
    """{subtipo: subcategoria_amenaza_o_None} — Regla B de asignar_amenazas()
    (calculo.py), ya no es una constante fija: sale de subtipos_catalogo.
    subcategoria_amenaza, editable directamente en Supabase o al añadir
    un subtipo nuevo desde la app."""
    res = sb.table("subtipos_catalogo").select("subtipo, subcategoria_amenaza").execute()
    return {r["subtipo"]: r["subcategoria_amenaza"] for r in res.data}


def cargar_subcategorias_amenaza_existentes():
    """Subcategorías ya en uso en el catálogo de amenazas — para ofrecerlas
    como sugerencia al fijar la de un subtipo, en vez de escribirlas a mano
    y arriesgarse a una errata que rompa la Regla B en silencio."""
    res = sb.table("catalogo_amenazas").select("subcategoria").execute()
    return sorted({r["subcategoria"] for r in res.data if r["subcategoria"]})


def anadir_subtipo_catalogo(subtipo, tipo, subcategoria=None):
    sb.table("subtipos_catalogo").insert(
        {"subtipo": subtipo.strip().upper(), "tipo_magerit": tipo,
         "subcategoria_amenaza": subcategoria or None}
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
    catalogo = cargar_catalogo()
    mapeo_subtipo_subcategoria = cargar_mapeo_subtipo_subcategoria()
    ajustes = cargar_ajustes(id_to_codigo)
    try:
        with st.spinner("Propagando valor y calculando impacto..."):
            valor_acum, amenazas_directas, filas_prop = ejecutar_recalculo(
                activos, dep_edges, per_edges, catalogo, mapeo_subtipo_subcategoria, ajustes
            )
        # Para la gestión de amenazas por activo (página Amenazas).
        st.session_state["catalogo"] = catalogo
        st.session_state["mapeo_subtipo_subcategoria"] = mapeo_subtipo_subcategoria
        st.session_state["ajustes"] = ajustes
        st.session_state["valor_acum"] = valor_acum
        st.session_state["amenazas_directas"] = amenazas_directas
        st.session_state["filas_prop"] = filas_prop
        st.success("Recalculado.")
    except ValueError as e:
        st.error(str(e))


def construir_tabla_detalle(filas_origen, sufijo_campo):
    """
    Una fila por (activo, amenaza) — no una por dimensión. 'Origen' queda
    fusionado: 'Directa' si es amenaza propia, o la procedencia (activo/
    persona de la que viene) si es heredada — Origen y Procedencia eran
    columnas redundantes. Una columna por dimensión en vez de una columna
    'Dimensión' + un valor suelto.
    """
    filas_det = []
    for f in filas_origen:
        valores_dim = {dim: f.get(f"{dim}_{sufijo_campo}") for dim in DIMENSIONES}
        if all(v is None for v in valores_dim.values()):
            continue
        etiqueta = "Directa" if f["origen"] == "Directa" else f["detalle_origen"]
        fila = {"Origen": etiqueta, "Amenaza": f["amenaza"], "Probabilidad": f["probabilidad"]}
        for dim in DIMENSIONES:
            fila[f"{dim} {sufijo_campo.capitalize()}"] = valores_dim[dim]
        fila["_max"] = max((v for v in valores_dim.values() if v is not None), default=0)
        filas_det.append(fila)
    return filas_det


def render_pagina_metrica(nombre_metrica, sufijo_campo, funcion_resumen, color_fn, key_prefix):
    """
    Vista resumen reutilizable para Impacto y Riesgo: dos submenús
    (Acumulado / Repercutido) según el origen de la amenaza, respetando
    la metodología (Directa -> valor acumulado; Dependencia/Persona
    asociada -> valor propio repercutido — ver Metodologia_Activos_y_
    Dependencias.md §6). Resumen por activo + detalle al seleccionar fila.
    """
    submenu = st.radio(
        f"Tipo de {nombre_metrica.lower()}",
        [f"{nombre_metrica} acumulado", f"{nombre_metrica} repercutido"],
        horizontal=True, key=f"{key_prefix}_submenu",
    )
    es_acumulado = submenu.endswith("acumulado")
    if es_acumulado:
        st.caption(f"{nombre_metrica} acumulado: amenazas propias del activo (origen Directa), "
                   "con el valor ACUMULADO del activo (Magerit Libro III).")
        filas_metrica = [f for f in st.session_state["filas_prop"] if f["origen"] == "Directa"]
    else:
        st.caption(f"{nombre_metrica} repercutido: amenazas heredadas por dependencia o persona "
                   "asociada, con el valor PROPIO del activo destino (Magerit Libro III).")
        filas_metrica = [f for f in st.session_state["filas_prop"] if f["origen"] != "Directa"]

    resumen = funcion_resumen(filas_metrica)
    campo_resumen = sufijo_campo  # "impacto" o "riesgo" -> misma clave que devuelve funcion_resumen

    filas_resumen = []
    for cod in opciones_codigo:
        r = resumen.get(cod)
        filas_resumen.append({
            "Código": cod, "Nombre": activos[cod]["nombre"], "Tipo": activos[cod]["tipo"],
            f"{nombre_metrica} máximo": r[campo_resumen] if r else None,
            "Dimensión": r["dimension"] if r else None,
        })
    df_resumen = pd.DataFrame(filas_resumen).sort_values(
        f"{nombre_metrica} máximo", ascending=False, na_position="last"
    ).reset_index(drop=True)

    st.caption(f"{nombre_metrica} máximo entre las amenazas de este origen y dimensiones de cada activo. "
               "Selecciona una fila para ver el detalle.")

    evento = st.dataframe(
        df_resumen.style.map(color_fn, subset=[f"{nombre_metrica} máximo"]),
        use_container_width=True, hide_index=True,
        on_select="rerun", selection_mode="single-row", key=f"{key_prefix}_tabla_resumen",
    )

    filas_seleccionadas = evento.selection.rows if evento and evento.selection else []
    if filas_seleccionadas and not df_resumen.empty:
        cod_detalle = df_resumen.iloc[filas_seleccionadas[0]]["Código"]
    elif not df_resumen.empty:
        cod_detalle = df_resumen.iloc[0]["Código"]
    else:
        cod_detalle = None

    st.subheader("Detalle de amenazas por activo")
    if cod_detalle is None:
        st.caption("No hay activos que mostrar.")
        return

    st.markdown(f"**{cod_detalle} · {activos[cod_detalle]['nombre']}**")
    detalle = [f for f in filas_metrica if f["codigo"] == cod_detalle]
    filas_det = construir_tabla_detalle(detalle, sufijo_campo)
    if not filas_det:
        st.caption(f"Este activo no tiene amenazas de este origen con {nombre_metrica.lower()} calculado.")
        return

    cols_dim = [f"{dim} {sufijo_campo.capitalize()}" for dim in DIMENSIONES]
    df_det = pd.DataFrame(filas_det).sort_values("_max", ascending=False).drop(columns="_max").reset_index(drop=True)
    st.dataframe(df_det.style.map(color_fn, subset=cols_dim), use_container_width=True, hide_index=True)


def construir_tabla_detalle_cualitativa(filas_origen, sufijo_campo, es_impacto):
    """Como construir_tabla_detalle: una fila por amenaza, una columna por
    dimensión (solo la letra: D/I/C/A/T). Impacto se muestra como banda de
    Valor (Despreciable...Extremo); Riesgo se deja en número (fórmula
    log-lineal, acotada a 0-10 pero sin niveles con nombre propio)."""
    filas_det = []
    for f in filas_origen:
        valores_dim = {dim: f.get(f"{dim}_{sufijo_campo}") for dim in DIMENSIONES}
        if all(v is None for v in valores_dim.values()):
            continue
        etiqueta = "Directa" if f["origen"] == "Directa" else f["detalle_origen"]
        fila = {"Origen": etiqueta, "Amenaza": f["amenaza"], "Probabilidad": f["probabilidad"]}
        presentes = [v for v in valores_dim.values() if v is not None]
        fila["_max"] = max(presentes, default=0)
        for dim in DIMENSIONES:
            val = valores_dim[dim]
            fila[dim] = valor_a_banda(val) if es_impacto else val
        filas_det.append(fila)
    return filas_det


def render_pagina_metrica_cualitativa(nombre_metrica, sufijo_campo, key_prefix):
    """
    Vista Cualitativa (Magerit Libro III §2.2.1): Impacto por la tabla de
    de degradación (§2.5.1, Valor x Degradación -> Impacto); Riesgo por la fórmula
    log-lineal (§6.3.3/§6.3.4, da más peso al Impacto que a la Probabilidad).
    Impacto se muestra siempre como banda de Valor (con sus colores);
    Riesgo se muestra en número, con su propia escala de colores.
    Misma estructura de submenús Acumulado/Repercutido que el método
    Cuantitativo (Metodologia_App_Riesgos.md §6).

    Tabla resumen: una fila por activo (todos, tengan o no amenazas
    calculadas), con una columna por dimensión D/I/C/A/T — cada una con
    el máximo de esa dimensión en concreto, no un único máximo global.
    """
    es_impacto = nombre_metrica == "Impacto"
    color_fn = color_valor_banda if es_impacto else color_riesgo

    submenu = st.radio(
        f"Tipo de {nombre_metrica.lower()}",
        [f"{nombre_metrica} acumulado", f"{nombre_metrica} repercutido"],
        horizontal=True, key=f"{key_prefix}_submenu",
    )
    es_acumulado = submenu.endswith("acumulado")
    if es_acumulado:
        st.caption(f"{nombre_metrica} acumulado: amenazas propias del activo (origen Directa), "
                   "con el valor ACUMULADO del activo.")
        filas_metrica = [f for f in st.session_state["filas_prop"] if f["origen"] == "Directa"]
    else:
        st.caption(f"{nombre_metrica} repercutido: amenazas heredadas por dependencia o persona "
                   "asociada, con el valor PROPIO del activo destino.")
        filas_metrica = [f for f in st.session_state["filas_prop"] if f["origen"] != "Directa"]

    matriz = matriz_por_activo(filas_metrica, sufijo_campo)

    filas_resumen = []
    for cod in opciones_codigo:
        valores_dim = matriz.get(cod, {d: None for d in DIMENSIONES})
        fila = {"Código": cod, "Nombre": activos[cod]["nombre"], "Tipo": activos[cod]["tipo"]}
        fila["_max"] = max((v for v in valores_dim.values() if v is not None), default=-1)
        for dim in DIMENSIONES:
            fila[dim] = valor_a_banda(valores_dim[dim]) if es_impacto else valores_dim[dim]
        filas_resumen.append(fila)
    df_resumen = pd.DataFrame(filas_resumen).sort_values(
        "_max", ascending=False
    ).drop(columns="_max").reset_index(drop=True)

    st.caption(f"{nombre_metrica} cualitativo por activo y dimensión. "
               "Selecciona una fila para ver el detalle de amenazas.")

    evento = st.dataframe(
        df_resumen.style.map(color_fn, subset=DIMENSIONES),
        use_container_width=True, hide_index=True,
        on_select="rerun", selection_mode="single-row", key=f"{key_prefix}_tabla_resumen",
    )

    filas_seleccionadas = evento.selection.rows if evento and evento.selection else []
    if filas_seleccionadas and not df_resumen.empty:
        cod_detalle = df_resumen.iloc[filas_seleccionadas[0]]["Código"]
    elif not df_resumen.empty:
        cod_detalle = df_resumen.iloc[0]["Código"]
    else:
        cod_detalle = None

    st.subheader("Detalle de amenazas por activo")
    if cod_detalle is None:
        st.caption("No hay activos que mostrar.")
        return

    st.markdown(f"**{cod_detalle} · {activos[cod_detalle]['nombre']}**")
    detalle = [f for f in filas_metrica if f["codigo"] == cod_detalle]
    filas_det = construir_tabla_detalle_cualitativa(detalle, sufijo_campo, es_impacto)
    if not filas_det:
        st.caption(f"Este activo no tiene amenazas de este origen con {nombre_metrica.lower()} calculado.")
        return

    df_det = pd.DataFrame(filas_det).sort_values("_max", ascending=False).drop(columns="_max").reset_index(drop=True)
    st.dataframe(df_det.style.map(color_fn, subset=DIMENSIONES), use_container_width=True, hide_index=True)


# ---------------------------------------------------------------------
# Selección de proyecto (barra lateral) — cada proyecto tiene sus propios
# activos/dependencias/personas; el catálogo de amenazas es común a todos.
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
    for _key in ["valor_acum", "amenazas_directas", "filas_prop", "ajustes", "catalogo",
                 "mapeo_subtipo_subcategoria"]:
        st.session_state.pop(_key, None)
st.session_state["_proyecto_anterior"] = proyecto_actual["id"]

st.sidebar.markdown("---")

# ---------------------------------------------------------------------
# Navegación
# ---------------------------------------------------------------------
pagina = st.sidebar.radio(
    "Navegación",
    ["Proyectos", "Activos", "Dependencias", "Personas asociadas", "Amenazas", "Impacto y Riesgo"],
)
# Submenú de "Impacto y Riesgo" — Streamlit no tiene menús anidados en una
# app de un solo archivo; se muestra como un segundo selector en la barra
# lateral, justo debajo, solo cuando está elegida esa entrada.
if pagina == "Impacto y Riesgo":
    pagina = st.sidebar.radio(
        "↳ Impacto y Riesgo", ["Resumen", "Impacto", "Riesgo"], key="submenu_impacto_riesgo",
    )

# Cambiar de página cancela cualquier edición en curso (activo o dependencia),
# para no dejar un formulario "a medias" abierto al volver más tarde.
if st.session_state.get("_pagina_anterior") not in (None, pagina):
    for _key in ["dep_editando", "accion_activo", "accion_activo_cod", "reabrir_nuevo_activo", "am_eliminar"]:
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
               "El catálogo de amenazas es común a todos los proyectos.")

    st.subheader(f"Proyectos existentes ({len(proyectos)})")
    filas_p = [{"Nombre": p["nombre"], "Sector": p.get("sector"), "Tamaño": p.get("tamano"),
                "Descripción": p.get("descripcion")} for p in proyectos]
    st.dataframe(pd.DataFrame(filas_p), use_container_width=True, hide_index=True)

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
                st.rerun()

    st.markdown("---")
    st.subheader("Eliminar proyecto")
    proyecto_a_borrar = st.selectbox("Proyecto a eliminar", nombres_proyecto, key="proyecto_a_borrar")
    pid_borrar = next(p["id"] for p in proyectos if p["nombre"] == proyecto_a_borrar)
    n_act_borrar = sb.table("activos").select("id", count="exact") \
        .eq("proyecto_id", pid_borrar).execute().count or 0
    st.warning(
        f"Se eliminarán también sus {n_act_borrar} activo(s) y todas las dependencias/personas "
        "asociadas de ese proyecto. Esta acción no se puede deshacer."
    )
    ultimo_proyecto = len(nombres_proyecto) <= 1
    if ultimo_proyecto:
        st.caption("No se puede eliminar el único proyecto que queda.")
    confirmar_borrado_p = st.checkbox(
        f"Sí, entiendo que se borrará el proyecto {proyecto_a_borrar} y todos sus datos",
        key="confirmar_borrado_proyecto", disabled=ultimo_proyecto,
    )
    if st.button("🗑️ Eliminar proyecto", disabled=not confirmar_borrado_p or ultimo_proyecto):
        sb.table("proyectos").delete().eq("id", pid_borrar).execute()
        st.success(f"Proyecto {proyecto_a_borrar} eliminado.")
        for _key in ["proyecto_nombre", "proyecto_id", "_proyecto_anterior",
                      "valor_acum", "amenazas_directas", "filas_prop"]:
            st.session_state.pop(_key, None)
        st.rerun()


# ---------------------------------------------------------------------
# Página: Activos
# ---------------------------------------------------------------------
elif pagina == "Activos":
    st.title("Activos")

    # --- Ventana emergente: alta de nuevo activo ---
    # Sin st.form a propósito: así los valores ya escritos se conservan si
    # la ventana se vuelve a abrir tras añadir un subtipo al catálogo.
    @_dialog("Nuevo activo", width="large")
    def dialogo_nuevo_activo():
        tipo_nuevo = st.selectbox("Tipo Magerit", TIPOS_MAGERIT, key="na_tipo")
        subtipos_disp_nuevo = subtipos_por_tipo.get(tipo_nuevo, [])

        with st.expander(f"➕ Añadir subtipo nuevo al catálogo (para {tipo_nuevo})"):
            csub1, csub2, csub3 = st.columns([2, 2, 1])
            nuevo_subtipo_txt = csub1.text_input("Nuevo subtipo", key="na_nuevo_subtipo_txt")
            subcats_nuevo = ["— sin asignar —"] + cargar_subcategorias_amenaza_existentes()
            subcat_nueva = csub2.selectbox(
                "Subcategoría de amenaza", subcats_nuevo, key="na_nuevo_subtipo_subcat",
                help="Regla B de asignación de amenazas — déjalo sin asignar si este subtipo "
                     "solo debe recibir las amenazas genéricas de su tipo.",
            )
            if csub3.button("Añadir", key="na_btn_add_subtipo"):
                if nuevo_subtipo_txt.strip():
                    try:
                        anadir_subtipo_catalogo(
                            nuevo_subtipo_txt, tipo_nuevo,
                            None if subcat_nueva == "— sin asignar —" else subcat_nueva,
                        )
                        # El rerun cierra la ventana: se vuelve a abrir sola.
                        st.session_state["reabrir_nuevo_activo"] = True
                        st.rerun()
                    except Exception as e:
                        st.error(f"No se pudo añadir (¿ya existe para este tipo?): {e}")
                else:
                    st.warning("Escribe un nombre de subtipo antes de añadir.")

        c1, c2 = st.columns(2)
        codigo = c1.text_input("Código (único dentro de este proyecto)", key="na_codigo")
        nombre = c2.text_input("Nombre", key="na_nombre")
        subtipos_sel = st.multiselect(
            "Subtipos", subtipos_disp_nuevo, key=f"na_subtipos_{tipo_nuevo}",
            help="Si el subtipo que necesitas no aparece, añádelo primero arriba al catálogo.",
        )

        st.caption("Valoración propia — deja 'n.a' si esa dimensión no aplica a este tipo de activo")
        cols_val = st.columns(5)
        valores = {dim: cols_val[i].selectbox(dim, VALORACION_OPCIONES, index=0, key=f"na_val_{dim}")
                   for i, dim in enumerate(DIMENSIONES)}

        b1, b2 = st.columns(2)
        if b1.button("Guardar activo", type="primary", use_container_width=True, key="na_guardar"):
            if not codigo or not nombre:
                st.error("Código y Nombre son obligatorios.")
            elif codigo in activos:
                st.error(f"Ya existe un activo con código {codigo} en este proyecto.")
            else:
                nuevo = sb.table("activos").insert({
                    "codigo": codigo, "nombre": nombre, "tipo": tipo_nuevo,
                    "proyecto_id": st.session_state["proyecto_id"],
                    **{f"valor_propio_{dim.lower()}": VALORACION_A_NUMERO[valores[dim]] for dim in DIMENSIONES},
                }).execute()
                activo_id = nuevo.data[0]["id"]
                for s_ in subtipos_sel:
                    sb.table("activo_subtipos").insert({"activo_id": activo_id, "subtipo": s_}).execute()
                _limpiar_estado_nuevo_activo()
                st.rerun()
        if b2.button("Cancelar", use_container_width=True, key="na_cancelar"):
            _limpiar_estado_nuevo_activo()
            st.rerun()

    def _limpiar_estado_nuevo_activo():
        for k in [k for k in st.session_state.keys() if str(k).startswith("na_")]:
            del st.session_state[k]
        st.session_state.pop("reabrir_nuevo_activo", None)

    cab1, cab2 = st.columns([4, 1])
    cab1.subheader(f"Activos existentes ({len(activos)})")
    if cab2.button("➕ Nuevo activo", type="primary", use_container_width=True):
        dialogo_nuevo_activo()
    elif st.session_state.pop("reabrir_nuevo_activo", False):
        dialogo_nuevo_activo()

    # --- Interruptor: valoración propia / valor acumulado ---
    ver_acumulado = st.toggle(
        "Ver valor acumulado", key="ver_valor_acumulado",
        help="Muestra en D/I/C/A/T el valor acumulado (máximo heredado de los activos "
             "que dependen de cada uno, personas incluidas) en lugar de la valoración propia.",
    )
    valor_acumulado_vista = {}
    if ver_acumulado:
        dep_edges_va, _ = cargar_dependencias(id_to_codigo)
        per_edges_va, _ = cargar_personas(id_to_codigo)
        try:
            valor_acumulado_vista = calcular_valor_acumulado(
                activos, dep_edges_va + [(srv, per, 100) for srv, per, _rol in per_edges_va], [],
            )
        except ValueError as e:
            st.error(str(e))

    tabs = st.tabs([f"{t} ({sum(1 for a in activos.values() if a['tipo']==t)})" for t in TIPOS_MAGERIT])
    seleccion_total = []  # (tipo_tab, codigo) de todas las pestañas

    for tipo_tab, tab in zip(TIPOS_MAGERIT, tabs):
        with tab:
            activos_tipo = [a for a in activos.values() if a["tipo"] == tipo_tab]
            if not activos_tipo:
                st.caption("No hay activos de este tipo.")
                continue

            reset_ctr = st.session_state.setdefault(f"reset_ctr_{tipo_tab}", 0)

            filas = []
            for a in activos_tipo:
                fila = {"Código": a["codigo"], "Nombre": a["nombre"], "Subtipo": a["subtipo"]}
                if ver_acumulado:
                    va = valor_acumulado_vista.get(a["codigo"], {})
                    fila.update({dim: valor_a_banda(va.get(dim)) for dim in DIMENSIONES})
                else:
                    vp = a["valor_propio"]
                    fila.update({dim: NUMERO_A_VALORACION.get(vp[dim], "n.a") for dim in DIMENSIONES})
                filas.append(fila)
            df_tipo = pd.DataFrame(filas)
            estilo_tabla = df_tipo.style.map(color_valor_banda, subset=DIMENSIONES) if ver_acumulado else df_tipo

            seleccionar_todos = st.checkbox(
                "Seleccionar todos", key=f"select_all_{tipo_tab}_{reset_ctr}",
                help="Aplica a todos los activos de esta pestaña, sin tener que marcarlos uno a uno en la tabla.",
            )

            evento_tabla = st.dataframe(
                estilo_tabla, use_container_width=True, hide_index=True,
                on_select="rerun", selection_mode="multi-row", key=f"tabla_activos_{tipo_tab}_{reset_ctr}",
            )
            if seleccionar_todos:
                codigos_sel = [a["codigo"] for a in activos_tipo]
            else:
                filas_sel = evento_tabla.selection.rows if evento_tabla and evento_tabla.selection else []
                codigos_sel = [df_tipo.iloc[i]["Código"] for i in filas_sel]
            seleccion_total.extend((tipo_tab, c) for c in codigos_sel)

            with st.expander(f"✏️ Aplicar valoración en bloque ({len(codigos_sel)} activo(s) seleccionado(s))",
                              expanded=len(codigos_sel) > 1):
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
                            actualizacion = {f"valor_propio_{d.lower()}": VALORACION_A_NUMERO[valor_bloque] for d in dims_sel}
                            ids_sel = [activos[c]["id"] for c in codigos_sel]
                            sb.table("activos").update(actualizacion).in_("id", ids_sel).execute()
                            # Nueva key -> la tabla y las casillas de esta pestaña nacen "limpias"
                            st.session_state[f"reset_ctr_{tipo_tab}"] = reset_ctr + 1
                            st.rerun()

    # --- Editar / Eliminar: solo con exactamente UN activo seleccionado ---
    def _cerrar_accion_activo(tipo_tab_sel=None):
        st.session_state.pop("accion_activo", None)
        st.session_state.pop("accion_activo_cod", None)
        if tipo_tab_sel:
            st.session_state[f"reset_ctr_{tipo_tab_sel}"] = st.session_state.get(f"reset_ctr_{tipo_tab_sel}", 0) + 1

    if len(seleccion_total) != 1:
        # Sin selección única, no hay acción abierta.
        st.session_state.pop("accion_activo", None)
        st.session_state.pop("accion_activo_cod", None)
    else:
        tipo_tab_sel, cod_editar = seleccion_total[0]
        activo_actual = activos[cod_editar]

        st.markdown("---")
        st.markdown(f"**Activo seleccionado:** {cod_editar} · {activo_actual['nombre']}")
        bcol1, bcol2, _esp = st.columns([1, 1, 3])
        if bcol1.button("✏️ Editar", key="btn_accion_editar", use_container_width=True):
            st.session_state["accion_activo"] = "editar"
            st.session_state["accion_activo_cod"] = cod_editar
        if bcol2.button("🗑️ Eliminar", key="btn_accion_eliminar", use_container_width=True):
            st.session_state["accion_activo"] = "eliminar"
            st.session_state["accion_activo_cod"] = cod_editar

        accion = st.session_state.get("accion_activo")
        if accion and st.session_state.get("accion_activo_cod") == cod_editar:

            if accion == "editar":
                st.markdown(f"#### Editando {cod_editar} · {activo_actual['nombre']}")

                tipo_editar = st.selectbox(
                    "Tipo Magerit", TIPOS_MAGERIT,
                    index=TIPOS_MAGERIT.index(activo_actual["tipo"]), key=f"tipo_editar_activo_{cod_editar}",
                )
                subtipos_disp_editar = subtipos_por_tipo.get(tipo_editar, [])
                subtipos_actuales = [s_.strip().upper() for s_ in (activo_actual["subtipo"] or "").split(";") if s_.strip()]
                subtipos_default = [s_ for s_ in subtipos_actuales if s_ in subtipos_disp_editar]
                subtipos_perdidos = [s_ for s_ in subtipos_actuales if s_ not in subtipos_disp_editar]
                if subtipos_perdidos and tipo_editar == activo_actual["tipo"]:
                    st.warning(
                        f"Estos subtipos del activo no están en el catálogo para {tipo_editar} "
                        f"y se perderán si guardas sin volver a añadirlos: {', '.join(subtipos_perdidos)}"
                    )

                with st.expander(f"➕ Añadir subtipo nuevo al catálogo (para {tipo_editar})"):
                    csub1e, csub2e, csub3e = st.columns([2, 2, 1])
                    nuevo_subtipo_e = csub1e.text_input("Nuevo subtipo", key=f"nuevo_subtipo_editar_txt_{cod_editar}")
                    subcats_e = ["— sin asignar —"] + cargar_subcategorias_amenaza_existentes()
                    subcat_nueva_e = csub2e.selectbox(
                        "Subcategoría de amenaza", subcats_e, key=f"nuevo_subtipo_subcat_editar_{cod_editar}",
                        help="Regla B de asignación de amenazas — déjalo sin asignar si este subtipo "
                             "solo debe recibir las amenazas genéricas de su tipo.",
                    )
                    if csub3e.button("Añadir", key=f"btn_add_subtipo_editar_{cod_editar}"):
                        if nuevo_subtipo_e.strip():
                            try:
                                anadir_subtipo_catalogo(
                                    nuevo_subtipo_e, tipo_editar,
                                    None if subcat_nueva_e == "— sin asignar —" else subcat_nueva_e,
                                )
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
                    cols_e = st.columns(5)
                    vp = activo_actual["valor_propio"]
                    valores_e = {dim: cols_e[i].selectbox(dim, VALORACION_OPCIONES, index=idx_valoracion(vp[dim]),
                                                          key=f"val_{dim.lower()}_e_{cod_editar}")
                                 for i, dim in enumerate(DIMENSIONES)}

                    fb1, fb2, _fesp = st.columns([1, 1, 3])
                    guardar = fb1.form_submit_button("Guardar cambios", type="primary", use_container_width=True)
                    cancelar = fb2.form_submit_button("Cancelar", use_container_width=True)

                    if guardar:
                        sb.table("activos").update({
                            "nombre": nombre_e, "tipo": tipo_editar,
                            **{f"valor_propio_{dim.lower()}": VALORACION_A_NUMERO[valores_e[dim]] for dim in DIMENSIONES},
                        }).eq("id", activo_actual["id"]).execute()
                        sb.table("activo_subtipos").delete().eq("activo_id", activo_actual["id"]).execute()
                        for s_ in subtipos_sel_e:
                            sb.table("activo_subtipos").insert({"activo_id": activo_actual["id"], "subtipo": s_}).execute()
                        _cerrar_accion_activo(tipo_tab_sel)
                        st.rerun()
                    if cancelar:
                        _cerrar_accion_activo()
                        st.rerun()

            elif accion == "eliminar":
                st.markdown(f"#### Eliminar {cod_editar} · {activo_actual['nombre']}")
                id_act = activo_actual["id"]
                n_dep = sb.table("dependencias").select("id", count="exact") \
                    .or_(f"activo_superior_id.eq.{id_act},activo_inferior_id.eq.{id_act}") \
                    .execute().count or 0
                n_per = sb.table("personas_asociadas").select("id", count="exact") \
                    .or_(f"activo_id.eq.{id_act},persona_activo_id.eq.{id_act}") \
                    .execute().count or 0
                if n_dep or n_per:
                    st.warning(
                        f"Este activo tiene {n_dep} dependencia(s) y {n_per} persona(s) asociada(s) "
                        "que se eliminarán también al borrarlo."
                    )
                confirmar_borrado = st.checkbox(
                    f"Sí, entiendo que se borrará {cod_editar} y sus relaciones asociadas",
                    key=f"confirmar_borrado_activo_{cod_editar}",
                )
                eb1, eb2, _eesp = st.columns([1, 1, 3])
                if eb1.button("🗑️ Eliminar activo", disabled=not confirmar_borrado, use_container_width=True):
                    sb.table("activos").delete().eq("id", id_act).execute()
                    _cerrar_accion_activo(tipo_tab_sel)
                    st.rerun()
                if eb2.button("Cancelar", key="cancelar_eliminar_activo", use_container_width=True):
                    _cerrar_accion_activo()
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
                cc1, cc2 = st.columns([6, 1.4])
                cc1.write(f"{cod_sup_r} · {activos[cod_sup_r]['nombre']}")
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
                cc1, cc2 = st.columns([6, 1.4])
                cc1.write(f"{cod_inf_r} · {activos[cod_inf_r]['nombre']}")
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
        c1, c2 = st.columns(2)
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
        etiqueta_grado_defecto = grado_a_banda_mas_cercana(default_grado)
        etiqueta_grado = st.selectbox(
            "Grado de dependencia", GRADO_OPCIONES,
            index=GRADO_OPCIONES.index(etiqueta_grado_defecto), key=f"grado_{identificador}",
        )
        grado = GRADO_A_NUMERO[etiqueta_grado]
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
    st.caption("Cada persona es un activo de tipo Personal del proyecto, asociado a un Servicio "
               "siempre al 100%. Se trata como una dependencia más: el Servicio hereda las amenazas "
               "que la Regla A/B asigna a ese activo Personal. El tipo de rol se guarda como dato, "
               "sin efecto en el cálculo por ahora.")

    servicios = [c for c in opciones_codigo if activos[c]["tipo"] == "SERVICIO"]
    personas_disp = [c for c in opciones_codigo if activos[c]["tipo"] == "PERSONAL"]

    if not servicios or not personas_disp:
        st.info("Para asociar personas hace falta al menos un activo de tipo Servicio y uno de tipo "
                "Personal en este proyecto. Créalos primero en la página Activos.")
    else:
        with st.form("nueva_persona", clear_on_submit=True):
            c1, c2, c3 = st.columns(3)
            cod_srv = c1.selectbox("Servicio", servicios,
                                   format_func=lambda c: f"{c} · {activos[c]['nombre']}")
            cod_per = c2.selectbox("Persona (activo Personal)", personas_disp,
                                   format_func=lambda c: f"{c} · {activos[c]['nombre']}")
            tipo_rol = c3.selectbox("Tipo de rol", ROLES)
            justificacion = st.text_area("Justificación", key="just_persona")

            if st.form_submit_button("Guardar persona asociada"):
                try:
                    sb.table("personas_asociadas").insert({
                        "activo_id": activos[cod_srv]["id"],
                        "persona_activo_id": activos[cod_per]["id"],
                        "tipo_rol": tipo_rol, "grado": 100,
                        "justificacion": justificacion or None,
                    }).execute()
                    st.rerun()
                except Exception as e:
                    st.error(f"No se pudo guardar (¿esta persona ya está asociada a este servicio?): {e}")

    per_edges, per_raw = cargar_personas(id_to_codigo)
    st.subheader(f"Personas asociadas existentes ({len(per_raw)})")
    if not per_raw:
        st.caption("— ninguna —")
    for r in sorted(per_raw, key=lambda r: (id_to_codigo[r["activo_id"]], id_to_codigo[r["persona_activo_id"]])):
        cod_srv_r = id_to_codigo[r["activo_id"]]
        cod_per_r = id_to_codigo[r["persona_activo_id"]]
        cc1, cc2, cc3, cc4 = st.columns([4, 4, 2, 1])
        cc1.write(f"{cod_srv_r} · {activos[cod_srv_r]['nombre']}")
        cc2.write(f"{cod_per_r} · {activos[cod_per_r]['nombre']}")
        cc3.caption(r["tipo_rol"])
        if cc4.button("🗑️", key=f"del_persona_{r['id']}", help="Eliminar esta asociación"):
            sb.table("personas_asociadas").delete().eq("id", r["id"]).execute()
            st.rerun()


# ---------------------------------------------------------------------
# Página: Amenazas (directas + heredadas, por activo)
# ---------------------------------------------------------------------
elif pagina == "Amenazas":
    st.title("Amenazas")

    if st.button("🔄 Recalcular", type="primary", key="recalc_amenazas"):
        recalcular_todo(activos, id_to_codigo)

    if "filas_prop" not in st.session_state or "catalogo" not in st.session_state:
        st.info("Pulsa Recalcular para ver los resultados.")
    elif not opciones_codigo:
        st.info("Todavía no hay activos dados de alta.")
    else:
        cod_sel = st.selectbox(
            "Activo", opciones_codigo,
            format_func=lambda c: f"{c} · {activos[c]['nombre']}", key="amenazas_activo",
        )
        activo_sel = activos[cod_sel]
        id_activo_sel = activo_sel["id"]

        # Asignación de la Regla A/B para este activo (sin ajustes) y con ajustes.
        base = asignar_amenazas(
            {cod_sel: activo_sel}, st.session_state["catalogo"],
            st.session_state["mapeo_subtipo_subcategoria"], filtrar=False,
        ).get(cod_sel, [])
        ajustes_activo = st.session_state.get("ajustes", {}).get(cod_sel, {})
        asignadas = aplicar_ajustes({cod_sel: base}, {cod_sel: ajustes_activo}).get(cod_sel, [])
        excluidas = [am for am in base if ajustes_activo.get(am["id"], {}).get("excluida")]
        base_por_id = {am["id"]: am for am in base}

        def _tras_cambio_ajuste():
            """Recalcula (los ajustes afectan también a los activos superiores)
            y limpia la selección de la tabla."""
            recalcular_todo(activos, id_to_codigo)
            st.session_state["reset_ctr_amenazas"] = st.session_state.get("reset_ctr_amenazas", 0) + 1
            st.rerun()

        # --- Añadir de nuevo una amenaza eliminada (solo las de la Regla A/B) ---
        with st.popover("➕ Añadir amenaza", disabled=not excluidas,
                        help="Solo se pueden volver a añadir amenazas que la Regla A/B asigna a este "
                             "activo y que se han eliminado antes." if excluidas else
                             "Este activo no tiene amenazas eliminadas."):
            am_recuperar = st.selectbox(
                "Amenaza eliminada", excluidas, format_func=lambda am: am["amenaza"], key=f"recuperar_{cod_sel}",
            )
            if st.button("Añadir al activo", type="primary", key=f"btn_recuperar_{cod_sel}"):
                # Vuelve con los valores del catálogo base: se borra el ajuste entero.
                sb.table("ajustes_amenaza_activo").delete() \
                    .eq("activo_id", id_activo_sel).eq("amenaza_id", am_recuperar["id"]).execute()
                _tras_cambio_ajuste()

        # --- Ventana emergente: editar una amenaza del activo ---
        @_dialog("Editar amenaza", width="large")
        def dialogo_editar_amenaza(am_sel, am_base):
            st.markdown(f"**{am_sel['amenaza']}** — {cod_sel} · {activo_sel['nombre']}")
            with st.form(f"ajuste_{cod_sel}_{am_sel['id']}"):
                prob_nueva = st.selectbox("Probabilidad", PROBABILIDADES,
                                          index=PROBABILIDADES.index(am_sel["probabilidad"]))
                st.caption("Degradación (%) — valores del catálogo base: " +
                           ", ".join(f"{d}={am_base.get(d)}" for d in DIMENSIONES) +
                           f" · probabilidad {am_base['probabilidad']}")
                cols_deg = st.columns(5)
                deg_nueva = {d: cols_deg[i].number_input(d, min_value=0, max_value=100, step=5,
                                                         value=int(am_sel.get(d) or 0))
                             for i, d in enumerate(DIMENSIONES)}
                fb1, fb2, fb3 = st.columns(3)
                guardar = fb1.form_submit_button("Guardar cambios", type="primary", use_container_width=True)
                cancelar = fb2.form_submit_button("Cancelar", use_container_width=True)
                restablecer = fb3.form_submit_button("↩️ Valores del catálogo", use_container_width=True,
                                                     disabled=not am_sel.get("ajustada"))

            if guardar:
                # Solo se guarda lo que difiere del catálogo base; el resto queda NULL.
                fila_aj = {
                    "activo_id": id_activo_sel, "amenaza_id": am_sel["id"], "excluida": False,
                    "probabilidad": None if prob_nueva == am_base["probabilidad"] else prob_nueva,
                    **{CAMPO_DEGRADACION[d]: (None if deg_nueva[d] == (am_base.get(d) or 0) else deg_nueva[d])
                       for d in DIMENSIONES},
                }
                if fila_aj["probabilidad"] is None and all(fila_aj[CAMPO_DEGRADACION[d]] is None for d in DIMENSIONES):
                    sb.table("ajustes_amenaza_activo").delete() \
                        .eq("activo_id", id_activo_sel).eq("amenaza_id", am_sel["id"]).execute()
                else:
                    sb.table("ajustes_amenaza_activo").upsert(fila_aj, on_conflict="activo_id,amenaza_id").execute()
                _tras_cambio_ajuste()
            if restablecer:
                sb.table("ajustes_amenaza_activo").delete() \
                    .eq("activo_id", id_activo_sel).eq("amenaza_id", am_sel["id"]).execute()
                _tras_cambio_ajuste()
            if cancelar:
                st.rerun()

        # --- Amenazas asignadas al activo ---
        st.subheader("Asignación directa")
        st.caption("Selecciona una amenaza para editar su probabilidad y degradación o eliminarla de este "
                   "activo. \"Cuenta para el activo = No\" significa que no tiene degradación en las "
                   "dimensiones relevantes de su tipo, pero se sigue propagando a los activos superiores.")
        if not asignadas:
            st.caption("Sin amenazas asignadas.")
        else:
            filas_d = [{
                "Amenaza": am["amenaza"], "Categoría": am.get("categoria"), "Subcategoría": am.get("subcategoria"),
                "Probabilidad": am["probabilidad"], **{d: am.get(d) for d in DIMENSIONES},
                "Ajustada": "✏️ Sí" if am.get("ajustada") else "",
                "Cuenta para el activo": "No" if _excluir_para_destino(activo_sel["tipo"], am) else "Sí",
            } for am in asignadas]
            df_d = pd.DataFrame(filas_d)
            reset_am = st.session_state.get("reset_ctr_amenazas", 0)
            evento_am = st.dataframe(
                df_d, use_container_width=True, hide_index=True,
                on_select="rerun", selection_mode="single-row", key=f"tabla_amenazas_{cod_sel}_{reset_am}",
            )
            sel_rows = evento_am.selection.rows if evento_am and evento_am.selection else []

            if not sel_rows:
                st.session_state.pop("am_eliminar", None)
            else:
                am_sel = asignadas[sel_rows[0]]
                am_base = base_por_id[am_sel["id"]]
                st.markdown(f"**Amenaza seleccionada:** {am_sel['amenaza']}")
                ab1, ab2, _aesp = st.columns([1, 1, 3])
                if ab1.button("✏️ Editar", key="btn_editar_amenaza", use_container_width=True):
                    st.session_state.pop("am_eliminar", None)
                    dialogo_editar_amenaza(am_sel, am_base)
                if ab2.button("🗑️ Eliminar", key="btn_eliminar_amenaza", use_container_width=True):
                    st.session_state["am_eliminar"] = am_sel["id"]

                if st.session_state.get("am_eliminar") == am_sel["id"]:
                    st.warning(f"Se eliminará **{am_sel['amenaza']}** de {cod_sel}. Dejará de propagarse a "
                               "los activos que dependen de él. Se puede recuperar después con "
                               "\"➕ Añadir amenaza\", y volverá con los valores del catálogo base.")
                    cb1, cb2, _cesp = st.columns([1, 1, 3])
                    if cb1.button("Confirmar eliminación", type="primary", key="confirmar_eliminar_amenaza",
                                  use_container_width=True):
                        sb.table("ajustes_amenaza_activo").upsert({
                            "activo_id": id_activo_sel, "amenaza_id": am_sel["id"], "excluida": True,
                            "probabilidad": None, **{CAMPO_DEGRADACION[d]: None for d in DIMENSIONES},
                        }, on_conflict="activo_id,amenaza_id").execute()
                        st.session_state.pop("am_eliminar", None)
                        _tras_cambio_ajuste()
                    if cb2.button("Cancelar", key="cancelar_eliminar_amenaza", use_container_width=True):
                        st.session_state.pop("am_eliminar", None)
                        st.rerun()

        if excluidas:
            st.caption("Eliminadas de este activo: " + ", ".join(am["amenaza"] for am in excluidas))

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
# Página: Resumen de Impacto y Riesgo (principales activos y amenazas)
# ---------------------------------------------------------------------
elif pagina == "Resumen":
    st.title("Impacto y Riesgo — Resumen")
    st.caption("Principales impactos y riesgos del proyecto (método Cualitativo, escala 0-10). "
               "Acumulado y repercutido se muestran por separado, como en el resto de la app.")

    if st.button("🔄 Recalcular", type="primary", key="recalc_resumen"):
        recalcular_todo(activos, id_to_codigo)

    if "filas_prop" not in st.session_state:
        st.info("Pulsa Recalcular para ver los resultados.")
    else:
        TOP_N = 10
        origen_vista = st.radio("Origen", ["Acumulado", "Repercutido"], horizontal=True, key="resumen_origen")
        if origen_vista == "Acumulado":
            st.caption("Acumulado: amenazas propias de cada activo, con su valor ACUMULADO.")
            filas_r = [f for f in st.session_state["filas_prop"] if f["origen"] == "Directa"]
        else:
            st.caption("Repercutido: amenazas heredadas por dependencia o persona asociada, con el valor "
                       "PROPIO del activo destino.")
            filas_r = [f for f in st.session_state["filas_prop"] if f["origen"] != "Directa"]

        def _maximo_por_clave(filas, campo, clave):
            """{clave(fila): (valor, dimension, fila)} con el mayor valor de
            '{dim}_{campo}' entre todas las dimensiones y filas de esa clave."""
            mejor = {}
            for f in filas:
                for dim in DIMENSIONES:
                    val = f.get(f"{dim}_{campo}")
                    if val is None:
                        continue
                    k = clave(f)
                    if k not in mejor or val > mejor[k][0]:
                        mejor[k] = (val, dim, f)
            return mejor

        def _tramo(val):
            if val >= 9.6:
                return "🟣 Extremo (9,6 – 10)"
            if val >= 8.6:
                return "🔴 Muy alto (8,6 – 9,5)"
            if val >= 6:
                return "🟠 Alto (6 – 8,5)"
            if val >= 3:
                return "🟡 Medio (3 – 5,9)"
            if val >= 1:
                return "🟢 Bajo (1 – 2,9)"
            return "⚪ Muy bajo (< 1)"

        riesgo_activo = _maximo_por_clave(filas_r, "riesgo_cualitativo", lambda f: f["codigo"])
        impacto_activo = _maximo_por_clave(filas_r, "impacto_cualitativo", lambda f: f["codigo"])

        if not riesgo_activo:
            st.caption("No hay amenazas de este origen con riesgo calculado.")
        else:
            # --- Indicadores: activos por tramo de riesgo máximo ---
            st.subheader("Activos por tramo de riesgo")
            tramos = ["🟣 Extremo (9,6 – 10)", "🔴 Muy alto (8,6 – 9,5)", "🟠 Alto (6 – 8,5)",
                      "🟡 Medio (3 – 5,9)", "🟢 Bajo (1 – 2,9)", "⚪ Muy bajo (< 1)"]
            conteo = {t: 0 for t in tramos}
            for val, _dim, _f in riesgo_activo.values():
                conteo[_tramo(val)] += 1
            cols_m = st.columns(6)
            for col, t in zip(cols_m, tramos):
                col.metric(t, conteo[t])
            st.caption(f"{len(riesgo_activo)} de {len(activos)} activos tienen algún riesgo de este origen.")

            def _tabla_top_activos(maximos, campo_titulo):
                es_impacto = campo_titulo == "Impacto"
                filas_t = []
                for cod, (val, dim, f) in maximos.items():
                    fila = {"Código": cod, "Nombre": activos[cod]["nombre"], "Tipo": activos[cod]["tipo"],
                            "_orden": val, campo_titulo: valor_a_banda(val) if es_impacto else val,
                            "Dimensión": dim, "Amenaza": f["amenaza"]}
                    if origen_vista == "Repercutido":
                        fila["Procedencia"] = f["detalle_origen"]
                    filas_t.append(fila)
                df_t = pd.DataFrame(filas_t).sort_values("_orden", ascending=False).head(TOP_N).drop(columns="_orden")
                color_fn, fmt = (color_valor_banda, {}) if es_impacto else (color_riesgo, {campo_titulo: "{:.2f}"})
                st.dataframe(df_t.style.map(color_fn, subset=[campo_titulo]).format(fmt),
                             use_container_width=True, hide_index=True)

            st.subheader(f"Activos con mayor riesgo (top {TOP_N})")
            _tabla_top_activos(riesgo_activo, "Riesgo")

            st.subheader(f"Activos con mayor impacto (top {TOP_N})")
            _tabla_top_activos(impacto_activo, "Impacto")

            # --- Combinaciones activo-amenaza más críticas ---
            st.subheader(f"Amenazas más críticas (top {TOP_N} activo-amenaza)")
            st.caption("Cada fila es una amenaza concreta sobre un activo concreto, en la dimensión donde "
                       "produce más riesgo — las primeras candidatas a tratar con salvaguardas.")
            riesgo_par = _maximo_por_clave(
                filas_r, "riesgo_cualitativo", lambda f: (f["codigo"], f["amenaza"], f["detalle_origen"]))
            filas_p = []
            for (cod, amenaza, detalle), (val, dim, f) in riesgo_par.items():
                fila = {"Código": cod, "Activo": activos[cod]["nombre"], "Amenaza": amenaza,
                        "Probabilidad": f["probabilidad"], "Dimensión": dim,
                        "Impacto": valor_a_banda(f.get(f"{dim}_impacto_cualitativo")), "Riesgo": val}
                if origen_vista == "Repercutido":
                    fila["Procedencia"] = detalle
                filas_p.append(fila)
            df_p = pd.DataFrame(filas_p).sort_values("Riesgo", ascending=False).head(TOP_N)
            st.dataframe(df_p.style.map(color_valor_banda, subset=["Impacto"])
                         .map(color_riesgo, subset=["Riesgo"]).format({"Riesgo": "{:.2f}"}),
                         use_container_width=True, hide_index=True)


# ---------------------------------------------------------------------
# Página: Impacto (vista resumen por activo, Acumulado / Repercutido)
# ---------------------------------------------------------------------
elif pagina == "Impacto":
    st.title("Impacto")
    st.caption("Vista Cualitativa (Magerit Libro III §2.2.1): Impacto por la tabla de degradación "
               "(Valor × Degradación, §2.5.1 de la metodología) — número continuo 0-10.")

    if st.button("🔄 Recalcular", type="primary", key="recalc_impacto"):
        recalcular_todo(activos, id_to_codigo)

    if "filas_prop" not in st.session_state:
        st.info("Pulsa Recalcular para ver los resultados.")
    else:
        render_pagina_metrica_cualitativa("Impacto", "impacto_cualitativo", "impacto")

        # --- Vista Cuantitativa (Impacto = valor x degradación, número real) ---
        # Aparcada por ahora a petición expresa — no se muestra en el menú,
        # pero el cálculo sigue disponible en cada fila de filas_prop
        # ("{dim}_impacto") y en calculo.impacto_maximo_por_activo(), por si
        # se quiere reactivar más adelante sin tener que rehacer nada.
        #
        # def color_impacto(val):
        #     if pd.isna(val):
        #         return ""
        #     if val < CORTE_BAJO:
        #         return "background-color: #d1fae5"
        #     if val < CORTE_MEDIO:
        #         return "background-color: #fef9c3"
        #     if val < CORTE_ALTO:
        #         return "background-color: #fed7aa"
        #     return "background-color: #fecaca"
        # render_pagina_metrica("Impacto", "impacto", impacto_maximo_por_activo, color_impacto, "impacto")


# ---------------------------------------------------------------------
# Página: Riesgo (vista resumen por activo, Acumulado / Repercutido)
# ---------------------------------------------------------------------
elif pagina == "Riesgo":
    st.title("Riesgo")
    st.caption("Vista Cualitativa (Magerit Libro III §2.2.1): fórmula log-lineal que da 1,5x más "
               "peso al Impacto que a la Probabilidad (§6.3.3/§6.3.4) — número continuo 0-10.")

    if st.button("🔄 Recalcular", type="primary", key="recalc_riesgo"):
        recalcular_todo(activos, id_to_codigo)

    if "filas_prop" not in st.session_state:
        st.info("Pulsa Recalcular para ver los resultados.")
    else:
        render_pagina_metrica_cualitativa("Riesgo", "riesgo_cualitativo", "riesgo")

        # --- Vista Cuantitativa (Riesgo = Impacto x ARO, número real sin acotar) ---
        # Aparcada por ahora a petición expresa — mismo motivo y misma forma
        # de reactivarla que en Impacto. El cálculo sigue en "{dim}_riesgo"
        # de cada fila y en calculo.riesgo_maximo_por_activo().
        #
        # todos_riesgos = [f.get(f"{dim}_riesgo") for f in st.session_state["filas_prop"] for dim in DIMENSIONES]
        # todos_riesgos = [v for v in todos_riesgos if v is not None and v > 0]
        # q1, q2, q3 = pd.Series(todos_riesgos).quantile([0.25, 0.5, 0.75]) if todos_riesgos else (0, 0, 0)
        # def color_riesgo(val):
        #     if pd.isna(val) or val == 0:
        #         return ""
        #     if val < q1:
        #         return "background-color: #d1fae5"
        #     if val < q2:
        #         return "background-color: #fef9c3"
        #     if val < q3:
        #         return "background-color: #fed7aa"
        #     return "background-color: #fecaca"
        # render_pagina_metrica("Riesgo", "riesgo", riesgo_maximo_por_activo, color_riesgo, "riesgo")
