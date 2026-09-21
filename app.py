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
    SUPABASE_SERVICE_ROLE_KEY = "..."   # la "service_role" key del proyecto
    (con RLS activado, la app se conecta con service_role para no quedar
    bloqueada por las políticas — no usar la clave "anon" aquí)

Requiere también haber ejecutado migracion_v2_subtipos_catalogo.sql y
migracion_multiproyecto.sql sobre la base de datos (añaden, respectivamente,
la tabla subtipos_catalogo y la tabla proyectos + activos.proyecto_id).
El árbol de dependencias usa st.graphviz_chart, que necesita el binario
`graphviz` instalado en el sistema (ver packages.txt si se despliega en
Streamlit Community Cloud).
"""

from collections import defaultdict
from datetime import date, datetime, timezone

import streamlit as st
import pandas as pd
from supabase import create_client

from calculo import (
    ejecutar_recalculo, construir_adj_forward, dependencias_transitivas,
    impacto_maximo_por_activo, riesgo_maximo_por_activo,
    matriz_por_activo, DIMENSIONES,
)

st.set_page_config(page_title="Modelo de riesgo TRC", layout="wide")

TIPOS_MAGERIT = ["INFORMACION", "SERVICIO", "SOFTWARE", "EQUIPAMIENTO",
                  "COMUNICACIONES", "INSTALACIONES", "PERSONAL"]
ROLES = ["Usuario", "Operador", "Administrador", "Desarrollador", "Responsable"]
VALORACION_OPCIONES = ["n.a", "Despreciable (0)", "Bajo (1.5)", "Medio (4)", "Alto (7)", "Muy Alto (9)", "Extremo (10)"]
VALORACION_A_NUMERO = {
    "n.a": None, "Despreciable (0)": 0.0, "Bajo (1.5)": 1.5, "Medio (4)": 4.0,
    "Alto (7)": 7.0, "Muy Alto (9)": 9.0, "Extremo (10)": 10.0,
}
NUMERO_A_VALORACION = {v: k for k, v in VALORACION_A_NUMERO.items()}

# Cortes de color para la vista de Impacto — los mismos que separan las
# categorías de valoración de activos (Bajo=1.5, Medio=4.0, Alto=7.0).
CORTE_BAJO, CORTE_MEDIO, CORTE_ALTO = 1.5, 4.0, 7.0

# Gestión de salvaguardas por activo (página Salvaguardas, pestaña Por activo).
APLICABILIDAD_OPCIONES = ["APLICA", "NO_APLICA", "NO_JUSTIFICADA"]
ESTADO_IMPLANTACION_OPCIONES = ["IMPLANTADA", "NO_IMPLANTADA", "NO_EVALUADA"]
FACTOR_MADUREZ = {
    "L0": 0.00,
    "L1": 0.10,
    "L2": 0.50,
    "L3": 0.80,
    "L4": 0.90,
    "L5": 1.00,
}


@st.cache_resource
def get_client():
    return create_client(
        st.secrets["SUPABASE_URL"],
        st.secrets["SUPABASE_SERVICE_ROLE_KEY"]
    )


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
    """Igual que cargar_dependencias: personas_asociadas no tiene
    proyecto_id propio, se filtra por los activos del proyecto activo."""
    ids_proyecto = list(id_to_codigo.keys())
    if not ids_proyecto:
        return [], []
    res = sb.table("personas_asociadas").select("*").in_("activo_id", ids_proyecto).execute()
    return [(id_to_codigo[r["activo_id"]], r["persona_nombre"], r["tipo_rol"], r["grado"])
            for r in res.data], res.data


def cargar_catalogo():
    """El catálogo de amenazas es GLOBAL: se comparte entre todos los
    proyectos (igual que la biblioteca de elementos en PILAR), por lo que
    no se filtra por proyecto."""
    res = sb.table("catalogo_amenazas").select(
        "*, categorias_amenaza(nombre), amenaza_tipo_activo(tipo_activo)"
    ).eq("vigente", True).execute()
    catalogo = []
    for row in res.data:
        catalogo.append({
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
    """{tipo_magerit: [subtipo, ...]} ordenado, desde la tabla subtipos_catalogo."""
    res = sb.table("subtipos_catalogo").select("*").execute()
    por_tipo = defaultdict(list)
    for r in res.data:
        por_tipo[r["tipo_magerit"]].append(r["subtipo"])
    return {k: sorted(v) for k, v in por_tipo.items()}


def cargar_salvaguardas_efectivas(id_to_codigo):
    """
    Lee de Supabase las salvaguardas EFECTIVAS de los activos del proyecto
    activo: solo activo_salvaguardas con aplicabilidad='APLICA' y
    estado_implantacion='IMPLANTADA', con evaluación de madurez vigente
    (evaluacion_madurez_salvaguardas.vigente=True) y solo relaciones
    salvaguarda_amenaza activas (activa=True).

    Nota: salvaguarda_amenaza.amenaza_id no tiene FK activa hacia
    catalogo_amenazas ahora mismo, así que ese cruce se hace aquí en
    Python (varias consultas) en vez de con un único select anidado de
    Supabase.

    Devuelve {(codigo_activo, nombre_amenaza): [ {codigo_activo,
    nombre_amenaza, codigo_salvaguarda, efecto_calculo, ep_base, ei_base,
    ep_efectiva, ei_efectiva, aplica_d, aplica_i, aplica_c, aplica_a,
    aplica_t, nivel_madurez, factor_madurez}, ... ]}
    Se usa tanto desde calculo.resolver_eficacia() (cálculo residual) como
    desde la página Salvaguardas (pestaña Por activo).
    """
    ids_proyecto = list(id_to_codigo.keys())
    if not ids_proyecto:
        return {}

    asal_res = sb.table("activo_salvaguardas").select(
        "id, activo_id, salvaguarda_id"
    ).eq("aplicabilidad", "APLICA").eq("estado_implantacion", "IMPLANTADA") \
        .in_("activo_id", ids_proyecto).execute()
    activo_salvaguardas = asal_res.data
    if not activo_salvaguardas:
        return {}

    asal_ids = [r["id"] for r in activo_salvaguardas]
    salvaguarda_ids = list({r["salvaguarda_id"] for r in activo_salvaguardas})

    salvaguardas_res = sb.table("salvaguardas").select("id, codigo, descripcion") \
        .in_("id", salvaguarda_ids).execute()
    salvaguarda_por_id = {s["id"]: s for s in salvaguardas_res.data}

    madurez_res = sb.table("evaluacion_madurez_salvaguardas").select(
        "activo_salvaguarda_id, nivel_madurez, factor_madurez"
    ).eq("vigente", True).in_("activo_salvaguarda_id", asal_ids).execute()
    madurez_por_asal = {m["activo_salvaguarda_id"]: m for m in madurez_res.data}

    rel_res = sb.table("salvaguarda_amenaza").select(
        "salvaguarda_id, amenaza_id, efecto_calculo, ep_base, ei_base, "
        "aplica_d, aplica_i, aplica_c, aplica_a, aplica_t"
    ).eq("activa", True).in_("salvaguarda_id", salvaguarda_ids).execute()
    relaciones = rel_res.data

    amenaza_ids = list({r["amenaza_id"] for r in relaciones})
    amenaza_por_id = {}
    if amenaza_ids:
        amenazas_res = sb.table("catalogo_amenazas").select("id, amenaza") \
            .in_("id", amenaza_ids).execute()
        amenaza_por_id = {a["id"]: a["amenaza"] for a in amenazas_res.data}

    relaciones_por_salvaguarda = defaultdict(list)
    for r in relaciones:
        relaciones_por_salvaguarda[r["salvaguarda_id"]].append(r)

    resultado = defaultdict(list)
    for asal in activo_salvaguardas:
        madurez = madurez_por_asal.get(asal["id"])
        if madurez is None:
            continue  # exige evaluación de madurez vigente
        salvaguarda = salvaguarda_por_id.get(asal["salvaguarda_id"])
        codigo_activo = id_to_codigo.get(asal["activo_id"])
        if salvaguarda is None or codigo_activo is None:
            continue
        factor_madurez = madurez["factor_madurez"]

        for rel in relaciones_por_salvaguarda.get(asal["salvaguarda_id"], []):
            nombre_amenaza = amenaza_por_id.get(rel["amenaza_id"])
            if nombre_amenaza is None:
                continue
            ep_base = rel["ep_base"]
            ei_base = rel["ei_base"]
            ep_efectiva = (ep_base * factor_madurez
                            if ep_base is not None and factor_madurez is not None else None)
            ei_efectiva = (ei_base * factor_madurez
                            if ei_base is not None and factor_madurez is not None else None)

            resultado[(codigo_activo, nombre_amenaza)].append({
                "codigo_activo": codigo_activo,
                "nombre_amenaza": nombre_amenaza,
                "codigo_salvaguarda": salvaguarda["codigo"],
                "efecto_calculo": rel["efecto_calculo"],
                "ep_base": ep_base,
                "ei_base": ei_base,
                "ep_efectiva": ep_efectiva,
                "ei_efectiva": ei_efectiva,
                "aplica_d": rel["aplica_d"], "aplica_i": rel["aplica_i"],
                "aplica_c": rel["aplica_c"], "aplica_a": rel["aplica_a"],
                "aplica_t": rel["aplica_t"],
                "nivel_madurez": madurez["nivel_madurez"],
                "factor_madurez": factor_madurez,
            })

    return dict(resultado)


def _combinar_eficacia_paquete(tipo_paquete, miembros):
    """Eficacia combinada de un paquete a partir de sus miembros no excluidos:
      TODAS   -> media
      ALGUNAS -> min(1.0, suma)
      UNA     -> máximo
    Si no queda ningún miembro válido tras excluir los NO_APLICA, 0.0.
    Usada por calculo.resolver_eficacia() cuando hay más de una salvaguarda
    efectiva para una misma amenaza + dimensión + efecto de cálculo."""
    validos = [m["eficacia_efectiva"] for m in miembros if not m["excluido"]]
    if not validos:
        return 0.0
    if tipo_paquete == "TODAS":
        return sum(validos) / len(validos)
    if tipo_paquete == "ALGUNAS":
        return min(1.0, sum(validos))
    if tipo_paquete == "UNA":
        return max(validos)
    raise ValueError(f"Tipo de paquete de salvaguardas desconocido: {tipo_paquete!r}")


def cargar_paquetes_salvaguardas(id_to_codigo):
    """
    Lee de Supabase los paquetes de salvaguardas (varias salvaguardas
    combinadas para una misma amenaza + dimensión + efecto de cálculo,
    p. ej. varias EP que juntas protegen la dimensión D frente a
    Ransomware) de los activos del proyecto activo. Se usa tanto desde
    calculo.resolver_eficacia() (cálculo residual) como desde la página
    Salvaguardas (pestaña Paquetes).

    Devuelve {(codigo_activo, nombre_amenaza, efecto_calculo, dimension):
    {"nombre", "tipo_paquete", "miembros": [...], "eficacia_combinada"}}.

    Cada miembro: {codigo_salvaguarda, aplicabilidad, estado_implantacion,
    nivel_madurez, factor_madurez, ep_base, ei_base, eficacia_efectiva,
    excluido}. Reglas de eficacia_efectiva por miembro:
      - aplicabilidad == "NO_APLICA"                          -> excluido=True
      - APLICA pero no IMPLANTADA                             -> 0.0
      - APLICA + IMPLANTADA sin evaluación de madurez vigente -> 0.0 (conservador)
      - APLICA + IMPLANTADA con evaluación vigente            -> base * factor_madurez
        (base = ep_base si el paquete es EP, ei_base si es EI)
    """
    ids_proyecto = list(id_to_codigo.keys())
    if not ids_proyecto:
        return {}

    paquetes_res = sb.table("paquetes_salvaguardas").select(
        "id, activo_id, amenaza_id, nombre, efecto_calculo, dimension, tipo_paquete"
    ).eq("activa", True).in_("activo_id", ids_proyecto).execute()
    paquetes = paquetes_res.data
    if not paquetes:
        return {}

    paquete_ids = [p["id"] for p in paquetes]
    miembros_res = sb.table("paquete_salvaguardas_miembros").select(
        "id, paquete_id, salvaguarda_amenaza_id, orden"
    ).in_("paquete_id", paquete_ids).order("orden").execute()
    miembros_por_paquete = defaultdict(list)
    for m in miembros_res.data:
        miembros_por_paquete[m["paquete_id"]].append(m)

    sa_ids = list({m["salvaguarda_amenaza_id"] for m in miembros_res.data})
    sa_por_id = {}
    if sa_ids:
        sa_res = sb.table("salvaguarda_amenaza").select(
            "id, salvaguarda_id, amenaza_id, efecto_calculo, ep_base, ei_base"
        ).in_("id", sa_ids).execute()
        sa_por_id = {r["id"]: r for r in sa_res.data}

    salvaguarda_ids = list({r["salvaguarda_id"] for r in sa_por_id.values()})
    salvaguarda_por_id = {}
    if salvaguarda_ids:
        salvaguardas_res = sb.table("salvaguardas").select("id, codigo, descripcion") \
            .in_("id", salvaguarda_ids).execute()
        salvaguarda_por_id = {s["id"]: s for s in salvaguardas_res.data}

    activo_ids_paquetes = list({p["activo_id"] for p in paquetes})
    activo_salvaguarda_por_par = {}
    asal_ids = []
    if activo_ids_paquetes and salvaguarda_ids:
        asal_res = sb.table("activo_salvaguardas").select(
            "id, activo_id, salvaguarda_id, aplicabilidad, estado_implantacion"
        ).in_("activo_id", activo_ids_paquetes).in_("salvaguarda_id", salvaguarda_ids).execute()
        for r in asal_res.data:
            activo_salvaguarda_por_par[(r["activo_id"], r["salvaguarda_id"])] = r
            asal_ids.append(r["id"])

    madurez_por_asal = {}
    if asal_ids:
        madurez_res = sb.table("evaluacion_madurez_salvaguardas").select(
            "activo_salvaguarda_id, nivel_madurez, factor_madurez"
        ).eq("vigente", True).in_("activo_salvaguarda_id", asal_ids).execute()
        madurez_por_asal = {r["activo_salvaguarda_id"]: r for r in madurez_res.data}

    amenaza_ids = list({p["amenaza_id"] for p in paquetes})
    amenaza_por_id = {}
    if amenaza_ids:
        amenazas_res = sb.table("catalogo_amenazas").select("id, amenaza") \
            .in_("id", amenaza_ids).execute()
        amenaza_por_id = {a["id"]: a["amenaza"] for a in amenazas_res.data}

    resultado = {}
    for p in paquetes:
        codigo_activo = id_to_codigo.get(p["activo_id"])
        nombre_amenaza = amenaza_por_id.get(p["amenaza_id"])
        if codigo_activo is None or nombre_amenaza is None:
            continue

        miembros_info = []
        for m in miembros_por_paquete.get(p["id"], []):
            sa = sa_por_id.get(m["salvaguarda_amenaza_id"])
            if sa is None:
                continue
            salvaguarda = salvaguarda_por_id.get(sa["salvaguarda_id"])
            if salvaguarda is None:
                continue
            asal = activo_salvaguarda_por_par.get((p["activo_id"], sa["salvaguarda_id"]))
            aplicabilidad = asal["aplicabilidad"] if asal else None
            estado_implantacion = asal["estado_implantacion"] if asal else None

            nivel_madurez = None
            factor_madurez = None
            madurez = madurez_por_asal.get(asal["id"]) if asal else None
            if madurez:
                nivel_madurez = madurez["nivel_madurez"]
                factor_madurez = madurez["factor_madurez"]

            ep_base = sa.get("ep_base")
            ei_base = sa.get("ei_base")
            base = ep_base if p["efecto_calculo"] == "EP" else ei_base

            excluido = aplicabilidad == "NO_APLICA"
            if excluido:
                eficacia_efectiva = None
            elif (aplicabilidad == "APLICA" and estado_implantacion == "IMPLANTADA"
                  and factor_madurez is not None):
                eficacia_efectiva = (base or 0.0) * factor_madurez
            else:
                # APLICA sin implantar, APLICA + IMPLANTADA sin evaluación
                # vigente, o sin fila en activo_salvaguardas: conservador, 0.
                eficacia_efectiva = 0.0

            miembros_info.append({
                "codigo_salvaguarda": salvaguarda["codigo"],
                "aplicabilidad": aplicabilidad,
                "estado_implantacion": estado_implantacion,
                "nivel_madurez": nivel_madurez,
                "factor_madurez": factor_madurez,
                "ep_base": ep_base,
                "ei_base": ei_base,
                "eficacia_efectiva": eficacia_efectiva,
                "excluido": excluido,
            })

        clave = (codigo_activo, nombre_amenaza, p["efecto_calculo"], p["dimension"])
        resultado[clave] = {
            "nombre": p["nombre"],
            "tipo_paquete": p["tipo_paquete"],
            "miembros": miembros_info,
            "eficacia_combinada": _combinar_eficacia_paquete(p["tipo_paquete"], miembros_info),
        }

    return resultado


def cargar_catalogo_salvaguardas():
    """Catálogo completo de salvaguardas (tabla salvaguardas), para la
    pestaña Catálogo de la página Salvaguardas — pura consulta, sin
    filtrar por proyecto ni activo (las salvaguardas son globales).
    Incluye 'id' (no se muestra en el catálogo, pero lo necesita el
    selector de la pestaña Por activo para guardar la relación)."""
    res = sb.table("salvaguardas").select(
        "id, codigo, descripcion, tipo, tipo_magerit, estado_eficacia, ei_base, ep_base"
    ).order("codigo").execute()
    return res.data


def cargar_detalle_salvaguardas_por_activo(activo_id):
    """
    Vista de auditoría para la pestaña "Por activo" de la página
    Salvaguardas: una fila por cada (activo_salvaguardas, salvaguarda_amenaza)
    del activo — a diferencia de cargar_salvaguardas_efectivas(), aquí se
    muestran TODAS las salvaguardas asociadas al activo, apliquen o no,
    estén implantadas o no, para poder auditarlas.

    Si una salvaguarda no tiene evaluación de madurez vigente, nivel y
    factor de madurez quedan en None (se muestran en blanco) y la
    eficacia efectiva correspondiente es 0.0 (si tiene base) o None (si
    ni siquiera tiene esa base, p. ej. ei_base en una salvaguarda solo EP).
    """
    asal_res = sb.table("activo_salvaguardas").select(
        "id, salvaguarda_id, aplicabilidad, estado_implantacion"
    ).eq("activo_id", activo_id).execute()
    activo_salvaguardas = asal_res.data
    if not activo_salvaguardas:
        return []

    salvaguarda_ids = list({r["salvaguarda_id"] for r in activo_salvaguardas})
    salvaguardas_res = sb.table("salvaguardas").select("id, codigo, descripcion") \
        .in_("id", salvaguarda_ids).execute()
    salvaguarda_por_id = {s["id"]: s for s in salvaguardas_res.data}

    asal_ids = [r["id"] for r in activo_salvaguardas]
    madurez_res = sb.table("evaluacion_madurez_salvaguardas").select(
        "activo_salvaguarda_id, nivel_madurez, factor_madurez"
    ).eq("vigente", True).in_("activo_salvaguarda_id", asal_ids).execute()
    madurez_por_asal = {m["activo_salvaguarda_id"]: m for m in madurez_res.data}

    sa_res = sb.table("salvaguarda_amenaza").select(
        "salvaguarda_id, amenaza_id, efecto_calculo, ep_base, ei_base, "
        "aplica_d, aplica_i, aplica_c, aplica_a, aplica_t"
    ).in_("salvaguarda_id", salvaguarda_ids).execute()
    relaciones_por_salvaguarda = defaultdict(list)
    for r in sa_res.data:
        relaciones_por_salvaguarda[r["salvaguarda_id"]].append(r)

    amenaza_ids = list({r["amenaza_id"] for r in sa_res.data})
    amenaza_por_id = {}
    if amenaza_ids:
        amenazas_res = sb.table("catalogo_amenazas").select("id, amenaza") \
            .in_("id", amenaza_ids).execute()
        amenaza_por_id = {a["id"]: a["amenaza"] for a in amenazas_res.data}

    filas = []
    for asal in activo_salvaguardas:
        salvaguarda = salvaguarda_por_id.get(asal["salvaguarda_id"])
        if salvaguarda is None:
            continue
        madurez = madurez_por_asal.get(asal["id"])
        nivel_madurez = madurez["nivel_madurez"] if madurez else None
        factor_madurez = madurez["factor_madurez"] if madurez else None

        for rel in relaciones_por_salvaguarda.get(asal["salvaguarda_id"], []):
            ep_base = rel.get("ep_base")
            ei_base = rel.get("ei_base")
            ep_efectiva = None if ep_base is None else (
                0.0 if factor_madurez is None else round(ep_base * factor_madurez, 4)
            )
            ei_efectiva = None if ei_base is None else (
                0.0 if factor_madurez is None else round(ei_base * factor_madurez, 4)
            )
            filas.append({
                "Salvaguarda": salvaguarda["codigo"],
                "Descripción": salvaguarda["descripcion"],
                "Amenaza": amenaza_por_id.get(rel["amenaza_id"]),
                "Aplicabilidad": asal["aplicabilidad"],
                "Estado implantación": asal["estado_implantacion"],
                "Efecto": rel.get("efecto_calculo"),
                "EP base": ep_base,
                "EI base": ei_base,
                "Nivel madurez": nivel_madurez,
                "Factor madurez": factor_madurez,
                "EP efectiva": ep_efectiva,
                "EI efectiva": ei_efectiva,
                "D": rel.get("aplica_d"), "I": rel.get("aplica_i"), "C": rel.get("aplica_c"),
                "A": rel.get("aplica_a"), "T": rel.get("aplica_t"),
            })
    return filas


def cargar_gestion_salvaguarda_activo(activo_id, salvaguarda_id):
    """Estado actual de una salvaguarda concreta en un activo concreto,
    para precargar el formulario "Gestionar salvaguarda del activo":
    la fila de activo_salvaguardas (si existe) y su evaluación de
    madurez vigente (si existe). None si el activo todavía no tiene
    ninguna relación con esa salvaguarda."""
    asal_res = sb.table("activo_salvaguardas").select(
        "id, aplicabilidad, estado_implantacion, justificacion_aplicabilidad, observaciones"
    ).eq("activo_id", activo_id).eq("salvaguarda_id", salvaguarda_id).execute()
    if not asal_res.data:
        return None
    asal = asal_res.data[0]

    madurez_res = sb.table("evaluacion_madurez_salvaguardas").select(
        "nivel_madurez, justificacion, resumen_evidencias, responsable_evaluacion"
    ).eq("activo_salvaguarda_id", asal["id"]).eq("vigente", True).execute()
    madurez = madurez_res.data[0] if madurez_res.data else None

    return {
        "aplicabilidad": asal["aplicabilidad"],
        "estado_implantacion": asal["estado_implantacion"],
        "justificacion_aplicabilidad": asal["justificacion_aplicabilidad"],
        "observaciones": asal["observaciones"],
        "nivel_madurez": madurez["nivel_madurez"] if madurez else None,
        "justificacion_madurez": madurez["justificacion"] if madurez else None,
        "resumen_evidencias": madurez["resumen_evidencias"] if madurez else None,
        "responsable_evaluacion": madurez["responsable_evaluacion"] if madurez else None,
    }


def guardar_gestion_salvaguarda(activo_id, salvaguarda_id, aplicabilidad, estado_implantacion,
                                 justificacion_aplicabilidad, observaciones,
                                 nivel_madurez=None, justificacion_madurez=None,
                                 resumen_evidencias=None, responsable_evaluacion=None):
    """
    Alta o edición de la relación activo_salvaguardas (única por
    activo_id + salvaguarda_id: actualiza si ya existe, inserta si no).

    Después, gestiona el histórico de evaluacion_madurez_salvaguardas:
    la evaluación vigente anterior (si la había) se marca vigente=false
    en cualquier caso, y solo si aplicabilidad=APLICA y
    estado_implantacion=IMPLANTADA se inserta una evaluación nueva con
    vigente=true — para el resto de combinaciones no se crea evaluación
    nueva, así que la salvaguarda queda sin ninguna vigente.
    """
    existente = sb.table("activo_salvaguardas").select("id") \
        .eq("activo_id", activo_id).eq("salvaguarda_id", salvaguarda_id).execute()

    datos_asal = {
        "aplicabilidad": aplicabilidad,
        "estado_implantacion": estado_implantacion,
        "justificacion_aplicabilidad": justificacion_aplicabilidad or None,
        "observaciones": observaciones or None,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    if existente.data:
        activo_salvaguarda_id = existente.data[0]["id"]
        sb.table("activo_salvaguardas").update(datos_asal).eq("id", activo_salvaguarda_id).execute()
    else:
        datos_asal["activo_id"] = activo_id
        datos_asal["salvaguarda_id"] = salvaguarda_id
        nuevo = sb.table("activo_salvaguardas").insert(datos_asal).execute()
        activo_salvaguarda_id = nuevo.data[0]["id"]

    # El histórico se conserva: la evaluación vigente anterior (si la
    # había) pasa a no vigente en cualquier caso.
    sb.table("evaluacion_madurez_salvaguardas").update({"vigente": False}) \
        .eq("activo_salvaguarda_id", activo_salvaguarda_id).eq("vigente", True).execute()

    if aplicabilidad == "APLICA" and estado_implantacion == "IMPLANTADA":
        sb.table("evaluacion_madurez_salvaguardas").insert({
            "activo_salvaguarda_id": activo_salvaguarda_id,
            "nivel_madurez": nivel_madurez,
            "factor_madurez": FACTOR_MADUREZ[nivel_madurez],
            "justificacion": justificacion_madurez or None,
            "resumen_evidencias": resumen_evidencias or None,
            "responsable_evaluacion": responsable_evaluacion or None,
            "fecha_evaluacion": date.today().isoformat(),
            "vigente": True,
        }).execute()


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
    catalogo = cargar_catalogo()
    salvaguardas_efectivas = cargar_salvaguardas_efectivas(id_to_codigo)
    paquetes_salvaguardas = cargar_paquetes_salvaguardas(id_to_codigo)
    try:
        with st.spinner("Propagando valor y calculando impacto..."):
            valor_acum, amenazas_directas, filas_prop = ejecutar_recalculo(
                activos, dep_edges, per_edges, catalogo,
                salvaguardas_efectivas=salvaguardas_efectivas,
                paquetes_salvaguardas=paquetes_salvaguardas,
            )
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


def color_cualitativo(val):
    """Mismos cortes que la escala de Valor de Magerit (§1.4): 1,5/4/7 —
    válido tanto para Impacto Cualitativo (tabla de degradación, 0-10) como para
    Riesgo Cualitativo (fórmula log-lineal, también acotado a 0-10)."""
    if pd.isna(val):
        return ""
    if val < CORTE_BAJO:
        return "background-color: #d1fae5"
    if val < CORTE_MEDIO:
        return "background-color: #fef9c3"
    if val < CORTE_ALTO:
        return "background-color: #fed7aa"
    return "background-color: #fecaca"


def construir_tabla_detalle_cualitativa(filas_origen, sufijo_campo):
    """Como construir_tabla_detalle: Impacto/Riesgo Cualitativo son ya
    números 0-10 (tabla de degradación / fórmula log-lineal), se ordenan como
    cualquier otro número — no como nivel ordinal."""
    filas_det = []
    for f in filas_origen:
        valores_dim = {dim: f.get(f"{dim}_{sufijo_campo}") for dim in DIMENSIONES}
        if all(v is None for v in valores_dim.values()):
            continue
        etiqueta = "Directa" if f["origen"] == "Directa" else f["detalle_origen"]
        fila = {"Origen": etiqueta, "Amenaza": f["amenaza"], "Probabilidad": f["probabilidad"]}
        nombre_columna = sufijo_campo.replace("_", " ").capitalize()
        for dim in DIMENSIONES:
            fila[f"{dim} {nombre_columna}"] = valores_dim[dim]
        presentes = [v for v in valores_dim.values() if v is not None]
        fila["_max"] = max(presentes, default=0)
        filas_det.append(fila)
    return filas_det


def _codigo_origen_amenaza(fila):
    """Deriva el activo origen de la amenaza de una fila de filas_prop, a
    partir de los campos 'origen'/'detalle_origen' que ya trae calculada
    calculo.propagar_e_calcular_impacto() — mismo criterio que usa esa
    función internamente (codigo_origen_amenaza), reconstruido aquí solo
    para mostrar en la UI de qué activo proceden las salvaguardas, sin
    tocar calculo.py ni recalcular nada:
      - Directa / Persona asociada directa -> el propio activo (fila['codigo'])
      - Dependencia -> el activo inferior, primer token de detalle_origen
      - Persona asociada vía dependencia -> el activo inferior tras " — vía "
    """
    origen = fila["origen"]
    detalle = fila["detalle_origen"]
    if origen == "Dependencia":
        return detalle.split(" · ")[0]
    if origen == "Persona asociada" and " — vía " in detalle:
        return detalle.split(" — vía ")[1].split(" · ")[0]
    return fila["codigo"]


def _procedencia_eficacia(codigo_origen, nombre_amenaza, efecto, dim,
                           salvaguardas_efectivas, paquetes_salvaguardas):
    """Solo para trazabilidad en la UI: de dónde sale la EP/EI efectiva de
    una dimensión (una única salvaguarda, o un paquete) — replica el
    mismo criterio de selección que calculo.resolver_eficacia() (efecto
    en (efecto,'AMBOS') y aplica_<dim>), pero no calcula ningún valor de
    riesgo: los números que se muestran siguen viniendo tal cual de
    filas_prop. None si no hay ninguna salvaguarda aplicable."""
    lista = salvaguardas_efectivas.get((codigo_origen, nombre_amenaza), [])
    dim_lower = dim.lower()
    matches = [sg for sg in lista
               if sg.get("efecto_calculo") in (efecto, "AMBOS")
               and sg.get(f"aplica_{dim_lower}")]
    if not matches:
        return None
    if len(matches) == 1:
        return {"tipo": "individual", "salvaguarda": matches[0]}
    paquete = paquetes_salvaguardas.get((codigo_origen, nombre_amenaza, efecto, dim))
    if paquete is None:
        return None
    return {"tipo": "paquete", "paquete": paquete}


def render_pagina_metrica_cualitativa(nombre_metrica, sufijo_campo, key_prefix):
    """
    Vista Cualitativa (Magerit Libro III §2.2.1): Impacto por la tabla de
    de degradación (§2.5.1, Valor x Degradación -> Impacto); Riesgo por la fórmula
    log-lineal (§6.3.3/§6.3.4, da más peso al Impacto que a la Probabilidad).
    Las dos salidas son números continuos 0-10, no niveles MB/B/M/A/MA.
    Misma estructura de submenús Acumulado/Repercutido que el método
    Cuantitativo (Metodologia_App_Riesgos.md §6).

    Tabla resumen: una fila por activo (todos, tengan o no amenazas
    calculadas), con una columna por dimensión D/I/C/A/T — cada una con
    el máximo de esa dimensión en concreto, no un único máximo global.
    """
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
        fila.update(valores_dim)
        fila["_max"] = max((v for v in valores_dim.values() if v is not None), default=-1)
        filas_resumen.append(fila)
    df_resumen = pd.DataFrame(filas_resumen).sort_values(
        "_max", ascending=False
    ).drop(columns="_max").reset_index(drop=True)

    st.caption(f"{nombre_metrica} cualitativo (0-10) por activo y dimensión. "
               "Selecciona una fila para ver el detalle de amenazas.")

    evento = st.dataframe(
        df_resumen.style.map(color_cualitativo, subset=DIMENSIONES),
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
    filas_det = construir_tabla_detalle_cualitativa(detalle, sufijo_campo)
    if not filas_det:
        st.caption(f"Este activo no tiene amenazas de este origen con {nombre_metrica.lower()} calculado.")
        return

    nombre_columna = sufijo_campo.replace("_", " ").capitalize()
    cols_dim = [f"{dim} {nombre_columna}" for dim in DIMENSIONES]
    df_det = pd.DataFrame(filas_det).sort_values("_max", ascending=False).drop(columns="_max").reset_index(drop=True)
    st.dataframe(df_det.style.map(color_cualitativo, subset=cols_dim), use_container_width=True, hide_index=True)

    # --- Trazabilidad del cálculo residual (solo en Riesgo) — todos los
    # valores vienen ya calculados en filas_prop, aquí solo se muestran.
    # La procedencia EP/EI (paquete o salvaguarda individual) se resuelve
    # con el mismo criterio que calculo.resolver_eficacia(), únicamente
    # para etiquetar de dónde sale cada eficacia, sin recalcular riesgo.
    if nombre_metrica == "Riesgo":
        st.subheader("Detalle del cálculo por amenaza")
        salvaguardas_efectivas = cargar_salvaguardas_efectivas(id_to_codigo)
        paquetes_salvaguardas = cargar_paquetes_salvaguardas(id_to_codigo)

        for idx, f in enumerate(detalle):
            etiqueta = f["amenaza"] if f["origen"] == "Directa" else f"{f['amenaza']} — {f['detalle_origen']}"
            codigo_origen = _codigo_origen_amenaza(f)

            filas_dim = []
            procedencias = []
            for dim in DIMENSIONES:
                if f.get(dim) is None:
                    continue
                filas_dim.append({
                    "Dimensión": dim,
                    "Degradación potencial": f.get(dim),
                    "EP efectiva/combinada": f.get(f"{dim}_ep_efectiva"),
                    "EI efectiva/combinada": f.get(f"{dim}_ei_efectiva"),
                    "ARO potencial": f.get("aro"),
                    "ARO residual": f.get(f"{dim}_aro_residual"),
                    "Degradación residual": f.get(f"{dim}_degradacion_residual"),
                    "Degradación residual repercutida": f.get(f"{dim}_rep_residual"),
                    "Impacto cualitativo potencial": f.get(f"{dim}_impacto_cualitativo"),
                    "Impacto cualitativo residual": f.get(f"{dim}_impacto_residual_cualitativo"),
                    "Riesgo cualitativo potencial": f.get(f"{dim}_riesgo_cualitativo"),
                    "Riesgo cualitativo residual": f.get(f"{dim}_riesgo_residual_cualitativo"),
                })
                for efecto in ("EP", "EI"):
                    proc = _procedencia_eficacia(codigo_origen, f["amenaza"], efecto, dim,
                                                  salvaguardas_efectivas, paquetes_salvaguardas)
                    if proc is None:
                        continue
                    if proc["tipo"] == "paquete":
                        paquete = proc["paquete"]
                        procedencias.append({
                            "Dimensión": dim, "Efecto": efecto,
                            "Tipo paquete": paquete["tipo_paquete"],
                            "Nombre paquete": paquete["nombre"],
                            "Miembros": " + ".join(m["codigo_salvaguarda"] for m in paquete["miembros"]),
                            "Eficacia": paquete["eficacia_combinada"],
                        })
                    else:
                        sg = proc["salvaguarda"]
                        procedencias.append({
                            "Dimensión": dim, "Efecto": efecto,
                            "Tipo paquete": "—", "Nombre paquete": "—",
                            "Miembros": sg["codigo_salvaguarda"],
                            "Eficacia": sg.get("ep_efectiva") if efecto == "EP" else sg.get("ei_efectiva"),
                        })

            if not filas_dim:
                continue

            with st.expander(f"Ver detalle del cálculo — {etiqueta}", key=f"{key_prefix}_detalle_calculo_{idx}"):
                st.dataframe(pd.DataFrame(filas_dim), use_container_width=True, hide_index=True)
                if procedencias:
                    st.caption("Procedencia de la eficacia EP/EI aplicada (paquete o salvaguarda individual):")
                    st.dataframe(pd.DataFrame(procedencias), use_container_width=True, hide_index=True)


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
    for _key in ["valor_acum", "amenazas_directas", "filas_prop"]:
        st.session_state.pop(_key, None)
st.session_state["_proyecto_anterior"] = proyecto_actual["id"]

st.sidebar.markdown("---")

# ---------------------------------------------------------------------
# Navegación
# ---------------------------------------------------------------------
pagina = st.sidebar.radio(
    "Navegación",
    ["Proyectos", "Activos", "Dependencias", "Personas asociadas", "Amenazas",
     "Salvaguardas", "Impacto", "Riesgo"],
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

    # --- Activos existentes (primero, agrupados por tipo) ---
    st.subheader(f"Activos existentes ({len(activos)})")

    tabs = st.tabs([f"{t} ({sum(1 for a in activos.values() if a['tipo']==t)})" for t in TIPOS_MAGERIT])

    for tipo_tab, tab in zip(TIPOS_MAGERIT, tabs):
        with tab:
            activos_tipo = [a for a in activos.values() if a["tipo"] == tipo_tab]
            if not activos_tipo:
                st.caption("No hay activos de este tipo.")
                continue

            reset_ctr = st.session_state.setdefault(f"reset_ctr_{tipo_tab}", 0)

            filas = []
            for a in activos_tipo:
                vp = a["valor_propio"]
                filas.append({
                    "Código": a["codigo"], "Nombre": a["nombre"], "Subtipo": a["subtipo"],
                    "D": NUMERO_A_VALORACION.get(vp["D"], vp["D"]), "I": NUMERO_A_VALORACION.get(vp["I"], vp["I"]),
                    "C": NUMERO_A_VALORACION.get(vp["C"], vp["C"]), "A": NUMERO_A_VALORACION.get(vp["A"], vp["A"]),
                    "T": NUMERO_A_VALORACION.get(vp["T"], vp["T"]),
                })
            df_tipo = pd.DataFrame(filas)

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
        codigo = c1.text_input("Código (único dentro de este proyecto)")
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
                st.error(f"Ya existe un activo con código {codigo} en este proyecto.")
            else:
                nuevo = sb.table("activos").insert({
                    "codigo": codigo, "nombre": nombre, "tipo": tipo_nuevo,
                    "proyecto_id": st.session_state["proyecto_id"],
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
    filas = [{"Activo": f"{a} · {activos[a]['nombre']}", "Persona/Rol": p, "Tipo de rol": r, "Grado": f"{g}%"}
             for a, p, r, g in per_edges]
    st.dataframe(pd.DataFrame(filas), use_container_width=True, hide_index=True)


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
# Página: Salvaguardas (catálogo global, detalle por activo y paquetes)
# ---------------------------------------------------------------------
elif pagina == "Salvaguardas":
    st.title("Salvaguardas")

    tab_catalogo, tab_por_activo, tab_paquetes = st.tabs(["Catálogo", "Por activo", "Paquetes"])

    with tab_catalogo:
        st.caption("Catálogo global de salvaguardas (tabla salvaguardas) — solo consulta.")
        catalogo_salv = cargar_catalogo_salvaguardas()
        if catalogo_salv:
            df_catalogo_salv = pd.DataFrame([
                {
                    "Código": s["codigo"],
                    "Descripción": s["descripcion"],
                    "Tipo MAGERIT": s.get("tipo_magerit"),
                    "Estado": s.get("estado_eficacia"),
                    "EI base": s.get("ei_base"),
                    "EP base": s.get("ep_base"),
                    "Tipo general": s["tipo"],
                }
                for s in catalogo_salv
            ])
            st.dataframe(df_catalogo_salv, use_container_width=True, hide_index=True)
        else:
            st.caption("No hay salvaguardas en el catálogo.")

    with tab_por_activo:
        if not opciones_codigo:
            st.info("Todavía no hay activos dados de alta.")
        else:
            cod_sel_salv = st.selectbox(
                "Activo", opciones_codigo,
                format_func=lambda c: f"{c} · {activos[c]['nombre']}", key="salvaguardas_activo",
            )
            st.caption("Todas las salvaguardas asociadas a este activo (aplique o no, "
                       "implantada o no), con su eficacia efectiva según la evaluación de "
                       "madurez vigente.")
            detalle_salv = cargar_detalle_salvaguardas_por_activo(activos[cod_sel_salv]["id"])
            if detalle_salv:
                st.dataframe(pd.DataFrame(detalle_salv), use_container_width=True, hide_index=True)
            else:
                st.caption(f"{cod_sel_salv} no tiene salvaguardas asociadas.")

            st.markdown("---")
            st.subheader("Gestionar salvaguarda del activo")

            if not catalogo_salv:
                st.caption("No hay salvaguardas en el catálogo todavía.")
            else:
                codigo_a_salvaguarda = {s["codigo"]: s for s in catalogo_salv}
                codigo_salvaguarda_sel = st.selectbox(
                    "Salvaguarda", sorted(codigo_a_salvaguarda.keys()),
                    format_func=lambda c: f"{c} · {codigo_a_salvaguarda[c]['descripcion']}",
                    key=f"gestion_salv_sel_{cod_sel_salv}",
                )
                activo_id_sel = activos[cod_sel_salv]["id"]
                salvaguarda_id_sel = codigo_a_salvaguarda[codigo_salvaguarda_sel]["id"]
                sufijo_key = f"{cod_sel_salv}_{codigo_salvaguarda_sel}"

                previo = cargar_gestion_salvaguarda_activo(activo_id_sel, salvaguarda_id_sel)

                idx_aplicabilidad = (
                    APLICABILIDAD_OPCIONES.index(previo["aplicabilidad"])
                    if previo and previo["aplicabilidad"] in APLICABILIDAD_OPCIONES else 0
                )
                idx_estado = (
                    ESTADO_IMPLANTACION_OPCIONES.index(previo["estado_implantacion"])
                    if previo and previo["estado_implantacion"] in ESTADO_IMPLANTACION_OPCIONES else 0
                )

                col_apl, col_est = st.columns(2)
                aplicabilidad_sel = col_apl.selectbox(
                    "Aplicabilidad", APLICABILIDAD_OPCIONES, index=idx_aplicabilidad,
                    key=f"gestion_salv_aplicabilidad_{sufijo_key}",
                )
                estado_sel = col_est.selectbox(
                    "Estado", ESTADO_IMPLANTACION_OPCIONES, index=idx_estado,
                    key=f"gestion_salv_estado_{sufijo_key}",
                )

                justificacion_aplicabilidad_sel = st.text_area(
                    "Justificación de aplicabilidad",
                    value=(previo.get("justificacion_aplicabilidad") or "") if previo else "",
                    key=f"gestion_salv_just_apl_{sufijo_key}",
                )
                observaciones_sel = st.text_area(
                    "Observaciones",
                    value=(previo.get("observaciones") or "") if previo else "",
                    key=f"gestion_salv_obs_{sufijo_key}",
                )

                nivel_madurez_sel = None
                justificacion_madurez_sel = ""
                resumen_evidencias_sel = ""
                responsable_evaluacion_sel = ""

                if aplicabilidad_sel == "APLICA" and estado_sel == "IMPLANTADA":
                    nivel_madurez_opciones = list(FACTOR_MADUREZ.keys())
                    idx_nivel = (
                        nivel_madurez_opciones.index(previo["nivel_madurez"])
                        if previo and previo.get("nivel_madurez") in nivel_madurez_opciones else 0
                    )
                    nivel_madurez_sel = st.selectbox(
                        "Nivel de madurez", nivel_madurez_opciones, index=idx_nivel,
                        format_func=lambda n: f"{n} → {FACTOR_MADUREZ[n] * 100:.0f} %",
                        key=f"gestion_salv_nivel_{sufijo_key}",
                    )
                    justificacion_madurez_sel = st.text_area(
                        "Justificación de madurez",
                        value=(previo.get("justificacion_madurez") or "") if previo else "",
                        key=f"gestion_salv_just_mad_{sufijo_key}",
                    )
                    resumen_evidencias_sel = st.text_area(
                        "Resumen de evidencias",
                        value=(previo.get("resumen_evidencias") or "") if previo else "",
                        key=f"gestion_salv_evidencias_{sufijo_key}",
                    )
                    responsable_evaluacion_sel = st.text_input(
                        "Responsable de evaluación",
                        value=(previo.get("responsable_evaluacion") or "") if previo else "",
                        key=f"gestion_salv_responsable_{sufijo_key}",
                    )

                if st.button("💾 Guardar", key=f"gestion_salv_guardar_{sufijo_key}"):
                    guardar_gestion_salvaguarda(
                        activo_id_sel, salvaguarda_id_sel,
                        aplicabilidad_sel, estado_sel,
                        justificacion_aplicabilidad_sel, observaciones_sel,
                        nivel_madurez=nivel_madurez_sel,
                        justificacion_madurez=justificacion_madurez_sel,
                        resumen_evidencias=resumen_evidencias_sel,
                        responsable_evaluacion=responsable_evaluacion_sel,
                    )
                    for _key in ["valor_acum", "amenazas_directas", "filas_prop"]:
                        st.session_state.pop(_key, None)
                    st.success(
                        f"Salvaguarda {codigo_salvaguarda_sel} guardada para {cod_sel_salv}. "
                        "Pulsa Recalcular en Impacto/Riesgo para actualizar el riesgo residual."
                    )
                    st.rerun()

    with tab_paquetes:
        if not opciones_codigo:
            st.info("Todavía no hay activos dados de alta.")
        else:
            st.caption("Paquetes de salvaguardas del activo seleccionado en la pestaña "
                       "\"Por activo\" — combinación TODAS/ALGUNAS/UNA cuando hay más de "
                       "una salvaguarda efectiva para la misma amenaza, efecto y dimensión.")
            paquetes_salv = cargar_paquetes_salvaguardas(id_to_codigo)
            filas_paquetes_salv = []
            for (cod_act, nom_am, efecto, dim), info in paquetes_salv.items():
                if cod_act == cod_sel_salv:
                    eficacias_individuales = ", ".join(
                        f"{m['codigo_salvaguarda']}="
                        + ("excluida" if m["excluido"] else str(m["eficacia_efectiva"]))
                        for m in info["miembros"]
                    )
                    filas_paquetes_salv.append({
                        "Amenaza": nom_am,
                        "Efecto": efecto,
                        "Dimensión": dim,
                        "Nombre paquete": info["nombre"],
                        "Tipo paquete": info["tipo_paquete"],
                        "Miembros": " + ".join(m["codigo_salvaguarda"] for m in info["miembros"]),
                        "Eficacias individuales": eficacias_individuales,
                        "Eficacia combinada": info["eficacia_combinada"],
                    })
            if filas_paquetes_salv:
                st.dataframe(pd.DataFrame(filas_paquetes_salv), use_container_width=True, hide_index=True)
            else:
                st.caption(f"{cod_sel_salv} no tiene paquetes de salvaguardas.")


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
        escenario_impacto = st.radio(
            "Escenario", ["Potencial", "Residual"], horizontal=True, key="impacto_escenario",
        )
        sufijo_impacto = (
            "impacto_cualitativo" if escenario_impacto == "Potencial"
            else "impacto_residual_cualitativo"
        )
        render_pagina_metrica_cualitativa("Impacto", sufijo_impacto, "impacto")

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
        escenario_riesgo = st.radio(
            "Escenario", ["Potencial", "Residual"], horizontal=True, key="riesgo_escenario",
        )
        sufijo_riesgo = (
            "riesgo_cualitativo" if escenario_riesgo == "Potencial"
            else "riesgo_residual_cualitativo"
        )
        render_pagina_metrica_cualitativa("Riesgo", sufijo_riesgo, "riesgo")

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
