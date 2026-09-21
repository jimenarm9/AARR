"""
calculo.py — Motor de cálculo del modelo de riesgo TRC (Magerit 3.0)

Reimplementa, sobre los datos leídos de Supabase, exactamente la misma
lógica validada en el modelo de Excel:
  - Regla A/B de asignación de amenazas (coincidencia estricta de tipo)
  - Filtro de exclusión por degradación 0, evaluado según el activo destino
  - Dimensión relevante por tipo (Servicio -> D, Información -> C/I/A/T)
  - Propagación de valor acumulado (Magerit Libro III, modelo cuantitativo)
  - Impacto: valor ACUMULADO x degradación para amenazas directas,
             valor PROPIO x degradación repercutida para las heredadas
             por dependencia o por persona asociada.

Cambios v2 (soporte a las páginas Amenazas / Impacto separadas y al
árbol de dependencias en la página Dependencias):
  - dependencias_transitivas() y construir_adj_forward() ahora son
    funciones de módulo reutilizables (antes vivían anidadas dentro de
    propagar_e_calcular_impacto), para poder pintar el árbol desde app.py.
  - Las filas "Directa" se calculan ahora para TODOS los activos, no
    solo para los que tienen dependencias o personas asociadas — así
    la página de Impacto puede mostrar el impacto máximo de cualquier
    activo, tenga o no relaciones.
  - Nueva función impacto_maximo_por_activo() para la vista resumen por activo.

Cambios v3 (menú de Riesgo):
  - Riesgo = Impacto x ARO (Magerit Libro I §3.1.7; Libro III §2.2.2),
    calculado en la misma fila que el Impacto, con el mismo criterio de
    origen: acumulado si es Directa, repercutido si es Dependencia o
    Persona asociada (no hace falta volver a distinguir el caso, el
    Impacto ya viene calculado con la fórmula correcta para su origen).
  - Nueva función riesgo_maximo_por_activo(), gemela de
    impacto_maximo_por_activo() pero sobre "{dim}_riesgo".
"""

from collections import defaultdict, deque
import math

DIMENSIONES = ["D", "I", "C", "A", "T"]

# ARO (Annual Rate of Occurrence) — Magerit Libro I, Tabla 2 (§3.1.2).
# Riesgo = Impacto x ARO (Libro I §3.1.7; Libro III §2.2.2, modelo cuantitativo).
ARO_POR_PROBABILIDAD = {
    "Muy Alta": 100, "Alta": 10, "Media": 1, "Baja": 0.1, "Muy Baja": 0.01,
}

# ---------------------------------------------------------------------
# Método CUALITATIVO — Magerit Libro III §2.2.1 (modelo cualitativo)
# ---------------------------------------------------------------------
# Impacto: tabla de degradación (Metodologia_App_Riesgos.md §2.5.1), en vez de
# la multiplicación v x d que sigue usando el método Cuantitativo (§6.4).
# Filas 100/90/50/10/1% con datos de referencia externos; fila 25%
# interpolada por TRC entre las filas 10% y 50%.
TABLA_IMPACTO_DEGRADACION = {
    100: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10],  # indice 0 = Valor 1 ... indice 9 = Valor 10
    90:  [1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
    50:  [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
    25:  [0, 0, 1, 2, 3, 4, 5, 6, 7, 8],
    10:  [0, 0, 0, 1, 2, 3, 4, 5, 6, 7],
    1:   [0, 0, 0, 0, 0, 0, 1, 2, 3, 4],
}


def degradacion_a_fila(degradacion_pct):
    """Redondea una degradación continua (0-100%, la que trae el catálogo)
    a una de las 6 filas de TABLA_IMPACTO_DEGRADACION, usando el punto medio
    entre filas consecutivas como corte. >70% cae en la fila 90/100%
    (da igual cuál, son idénticas en la tabla)."""
    if degradacion_pct is None or degradacion_pct <= 0:
        return None
    if degradacion_pct <= 5.5:
        return 1
    if degradacion_pct <= 17.5:
        return 10
    if degradacion_pct <= 37.5:
        return 25
    if degradacion_pct <= 70:
        return 50
    return 90


def impacto_tabla(valor, degradacion_pct):
    """
    Impacto por tabla de degradación — método Cualitativo (§2.5.1). Sustituye
    a `valor x degradación` solo para este método; el Cuantitativo
    (§6.4) sigue usando la multiplicación directa.
    """
    if valor is None or valor <= 0 or degradacion_pct is None or degradacion_pct <= 0:
        return 0.0
    fila = degradacion_a_fila(degradacion_pct)
    valor_idx = max(1, min(10, round(valor))) - 1
    return float(TABLA_IMPACTO_DEGRADACION[fila][valor_idx])


# Riesgo cualitativo: fórmula log-lineal (Metodologia §6.3.3/§6.3.4),
# calibrada para dar 1,5x más peso al Impacto que a la Probabilidad
# (axioma de Magerit §2.2.1: "más peso al impacto que a la probabilidad"),
# reescalada para que el peor caso posible (Impacto=10, Muy Alta) llegue
# a 10. Nunca usa tabla de niveles — el resultado es un número continuo
# 0-10, no una etiqueta MB/B/M/A/MA.
_A_RIESGO_CUALITATIVO = 0.771
_B_RIESGO_CUALITATIVO = 1.147


def riesgo_cualitativo_formula(impacto, aro):
    """
    si Impacto == 0: Riesgo = 0                     (axioma ℜ(0,p)=0)
    si no: Riesgo = max(0,01; a×Impacto + b×log10(ARO)), acotado a 10
    ℜ(v,0)=0 sí hay que tratarlo aparte desde que existe el ARO residual
    (una EP del 100% deja aro_residual=0): sin salvaguardas la Probabilidad
    nunca era 0 (el mínimo real era Muy Baja/ARO=0,01), pero con el cálculo
    residual sí puede llegar a serlo, y entonces el riesgo debe ser 0, no 0.01.
    """
    if impacto is None or impacto <= 0:
        return 0.0
    if aro is None or aro <= 0:
        return 0.0
    bruto = _A_RIESGO_CUALITATIVO * impacto + _B_RIESGO_CUALITATIVO * math.log10(aro)
    return round(min(10.0, max(0.01, bruto)), 2)


# Dimensión relevante según el tipo de activo. El resto se deja en blanco
# (no en 0) para no confundir "no aplica" con "amenaza sin impacto".
DIM_RELEVANTE = {"SERVICIO": ["D"], "INFORMACION": ["C", "I", "A", "T"]}

# SUBTIPO de activo -> Subcategoría de amenaza. Vive aquí como diccionario
# de Python (no como tabla) para mantener la app simple; si el catálogo
# crece mucho, puede moverse a una tabla `subtipo_subcategoria` editable
# sin tocar código.
SUBTIPO_TO_SUBCATEGORIA = {
    "CERTIFICADOS": "Certificados", "CORREO": "Correo", "DNS": "DNS",
    "MOVIL": "Movil", "WEB": "Web", "ADMINISTRADORES": "Credenciales",
    "CIBERSEGURIDAD": "Seguridad", "DOMINIO": "Credenciales",
    "DESARROLLO": None, "FIREWALL": None,
    "MAQUINA_VIRTUAL": "Maquina Virtual", "HYPERVISOR": "Maquina Virtual",
    "CLOUD": "Cloud", "SUBCONTRATADO": "Subcontratado",
}


def _mask(tipo, dim, val):
    """Deja en None (blanco) la dimensión que no aplica a este tipo."""
    if tipo not in DIM_RELEVANTE:
        return val
    return val if dim in DIM_RELEVANTE[tipo] else None


def _excluir_para_destino(tipo_destino, amenaza):
    """Filtro de degradación 0, evaluado por el tipo del activo destino."""
    if tipo_destino == "SERVICIO":
        return (amenaza.get("D") or 0) == 0
    if tipo_destino == "INFORMACION":
        return all((amenaza.get(d) or 0) == 0 for d in ["C", "I", "A", "T"])
    return False


def calcular_valor_acumulado(activos, dep_edges, per_edges):
    """
    activos: {codigo: {..., 'valor_propio': {D,I,C,A,T}}}
    dep_edges: lista de (superior, inferior, grado)
    per_edges: lista de (activo, persona_nombre, rol, grado)  [no se usa
               para propagar valor -> el personal no acumula, Magerit
               dice que no suele tener dependencias hacia abajo]

    Modelo CUALITATIVO de Magerit (Libro III, Análisis algorítmico):
        valor_acumulado(B) = max(valor_propio(B), max{valor_acumulado(Ai)})
    para cada superior Ai de B, por cada dimensión por separado.

    Se usa el máximo (no la suma) porque la escala de valoración 0-10 es
    logarítmica/ordinal (Libro II cap. 4: "siempre es igual de relevante
    que un activo sea el doble de valioso que otro, independientemente
    de su valor absoluto") — sumar puntos de una escala así no tiene una
    interpretación coherente y puede desbordar el rango 0-10 sin que
    exista ningún nivel definido por encima de 10 (Extremo). El modelo
    de SUMA (cuantitativo) es el que Magerit reserva para valoraciones
    económicas/lineales reales (dinero), no para esta escala ordinal.

    Nota: en este modelo, el grado de dependencia NO atenúa el valor
    heredado — un superior al 10% aporta su valor completo al máximo,
    igual que uno al 100%. Es la fórmula tal cual la define Magerit para
    el modelo cualitativo, distinta del peso que sí tiene el grado en la
    degradación repercutida (ver propagar_e_calcular_impacto).

    Devuelve {codigo: {D,I,C,A,T}} con el valor acumulado por dimensión,
    siempre dentro de la escala 0-10 de los valores propios de entrada.
    Lanza ValueError si detecta un ciclo en el grafo de dependencias.
    """
    adj = defaultdict(list)  # inferior -> [(superior, grado)]
    inferiores_de = defaultdict(list)  # superior -> [inferior]
    for sup, inf, grado in dep_edges:
        adj[inf].append((sup, grado))
        inferiores_de[sup].append(inf)

    in_degree = {cod: len(adj.get(cod, [])) for cod in activos}
    queue = deque([c for c in activos if in_degree[c] == 0])
    orden = []
    while queue:
        n = queue.popleft()
        orden.append(n)
        for inf in inferiores_de.get(n, []):
            in_degree[inf] -= 1
            if in_degree[inf] == 0:
                queue.append(inf)

    if len(orden) != len(activos):
        faltan = set(activos) - set(orden)
        raise ValueError(f"Ciclo detectado en las dependencias, involucra a: {faltan}")

    valor_acumulado = {}
    for cod in orden:
        propio = activos[cod]["valor_propio"]
        va = {}
        for dim in DIMENSIONES:
            candidatos = [propio.get(dim) or 0.0]
            for sup, grado in adj.get(cod, []):
                candidatos.append((valor_acumulado.get(sup, {}).get(dim)) or 0.0)
            va[dim] = round(max(candidatos), 2)
        valor_acumulado[cod] = va
    return valor_acumulado


def asignar_amenazas(activos, catalogo, filtrar=True):
    """
    catalogo: lista de amenazas, cada una con 'tipos_activo' (lista, ya
              expandida si el 'Activo principal' original tenía varios),
              'subcategoria', 'probabilidad', D/I/C/A/T.
    Devuelve {codigo: [amenaza, ...]} — Regla A (genérica) + Regla B
    (subtipo -> subcategoría), coincidencia estricta de tipo.
    Si filtrar=True aplica ya el filtro de degradación 0 (para la vista
    "asignación directa"); si False, deja todo sin filtrar (fuente para
    la propagación, que decide la exclusión por cada activo destino).
    """
    por_tipo = defaultdict(list)
    for am in catalogo:
        for t in am["tipos_activo"]:
            por_tipo[t].append(am)

    resultado = defaultdict(list)
    for cod, a in activos.items():
        tipo = a["tipo"]
        subtipos = [s.strip().upper() for s in (a.get("subtipo") or "").split(";") if s.strip()]

        for am in por_tipo.get(tipo, []):
            if am["subcategoria"]:
                continue  # genericas solamente aqui
            if filtrar and _excluir_para_destino(tipo, am):
                continue
            resultado[cod].append(am)

        for subtipo in subtipos:
            subcat = SUBTIPO_TO_SUBCATEGORIA.get(subtipo)
            if not subcat:
                continue
            for am in por_tipo.get(tipo, []):
                if am["subcategoria"] == subcat:
                    if filtrar and _excluir_para_destino(tipo, am):
                        continue
                    resultado[cod].append(am)

    # deduplicar por nombre+subcategoria dentro de cada activo
    for cod in resultado:
        vistos = set()
        dedup = []
        for am in resultado[cod]:
            key = (am["amenaza"], am["subcategoria"])
            if key in vistos:
                continue
            vistos.add(key)
            dedup.append(am)
        resultado[cod] = dedup

    return resultado


def construir_adj_forward(dep_edges):
    """superior -> [(inferior, grado), ...]. Reutilizable desde app.py
    para pintar el árbol de dependencias de un activo concreto."""
    adj = defaultdict(list)
    for sup, inf, grado in dep_edges:
        adj[sup].append((inf, grado))
    return adj


def dependencias_transitivas(origen, dep_adj_forward):
    """
    Devuelve {inferior: grado_transitivo} — todo lo alcanzable hacia
    abajo desde 'origen'.

    Cuando un activo es alcanzable por VARIOS caminos (estructuras de
    diamante — p. ej. A depende de B1 y B2, y ambos dependen de C),
    Magerit exige combinar los caminos con esta fórmula (Libro III
    §2.2.2, "Las dependencias entre activos"):

        grado(A⇒C) = Σᵢ { grado(A⇒Bi) × grado(Bi→C) }

    donde la suma NO es aritmética — se realiza como
    a + b = 1 − (1−a)×(1−b) (la "suma de Bayes"), precisamente para
    que la dependencia total nunca pueda superar el 100% por muchos
    caminos que se combinen. ANTES este código se quedaba con el
    camino de mayor grado en vez de combinarlos — quedó corregido
    tras revisar el texto exacto de Magerit.
    """
    # 1. Encontrar el subgrafo alcanzable desde origen, y para cada nodo
    #    alcanzable, quiénes son sus predecesores DIRECTOS dentro de ese
    #    subgrafo (para poder combinar sus contribuciones).
    alcanzables = set()
    stack = [origen]
    predecesores = defaultdict(list)  # nodo -> [(predecesor_directo, grado_arista)]
    while stack:
        actual = stack.pop()
        if actual in alcanzables:
            continue
        alcanzables.add(actual)
        for inferior, grado in dep_adj_forward.get(actual, []):
            predecesores[inferior].append((actual, grado))
            if inferior not in alcanzables:
                stack.append(inferior)

    # 2. Orden topológico del subgrafo alcanzable, para poder calcular el
    #    grado_transitivo de cada nodo solo después de tener resueltos
    #    TODOS sus predecesores directos alcanzables.
    sucesores = defaultdict(list)
    for n in alcanzables:
        for inf, _grado in dep_adj_forward.get(n, []):
            if inf in alcanzables:
                sucesores[n].append(inf)

    in_degree = {n: len(predecesores.get(n, [])) for n in alcanzables}
    cola = deque([n for n in alcanzables if in_degree[n] == 0])  # origen entra aqui (sin predecesores dentro del alcanzable)
    orden = []
    while cola:
        n = cola.popleft()
        orden.append(n)
        for s in sucesores.get(n, []):
            in_degree[s] -= 1
            if in_degree[s] == 0:
                cola.append(s)

    # 3. Combinar, en orden topológico, las contribuciones de cada
    #    predecesor directo con la suma de Bayes.
    grado_transitivo_frac = {origen: 1.0}
    for nodo in orden:
        if nodo == origen:
            continue
        combinado = 0.0
        for pred, grado_arista in predecesores.get(nodo, []):
            g_pred = grado_transitivo_frac.get(pred)
            if g_pred is None:
                continue
            contribucion = g_pred * (grado_arista / 100.0)
            combinado = combinado + contribucion - combinado * contribucion
        grado_transitivo_frac[nodo] = combinado

    del grado_transitivo_frac[origen]
    return {nodo: round(frac * 100.0, 4) for nodo, frac in grado_transitivo_frac.items()}


def resolver_eficacia(codigo_origen_amenaza, nombre_amenaza, efecto, dimension,
                       salvaguardas_efectivas, paquetes_salvaguardas):
    """
    Resuelve la eficacia (EP o EI, según `efecto`) aplicable a una
    dimensión, para las salvaguardas del activo origen de la amenaza:
      - 0 salvaguardas aplicables (con ese efecto y esa dimensión) -> 0.0
      - 1 salvaguarda aplicable -> su ep_efectiva/ei_efectiva directamente
      - >1 salvaguardas aplicables -> busca el paquete
        (codigo_origen_amenaza, nombre_amenaza, efecto, dimension) en
        paquetes_salvaguardas (ver app.cargar_paquetes_salvaguardas):
          - si no existe, ValueError (falta definir la regla de combinación)
          - si existe, exige que el paquete incluya TODAS las salvaguardas
            aplicables (si falta alguna, ValueError) y devuelve su
            "eficacia_combinada" ya calculada por el cargador — nunca se
            combinan aquí salvaguardas sueltas con MAX/suma/Bayes.
    """
    salvaguardas_amenaza = salvaguardas_efectivas.get(
        (codigo_origen_amenaza, nombre_amenaza), []
    )
    dim_lower = dimension.lower()
    matches = [sg for sg in salvaguardas_amenaza
               if sg.get("efecto_calculo") in (efecto, "AMBOS")
               and sg.get(f"aplica_{dim_lower}")]

    if not matches:
        return 0.0

    if len(matches) == 1:
        campo = "ep_efectiva" if efecto == "EP" else "ei_efectiva"
        return matches[0].get(campo) or 0.0

    paquete = paquetes_salvaguardas.get(
        (codigo_origen_amenaza, nombre_amenaza, efecto, dimension)
    )
    if paquete is None:
        raise ValueError(
            f"Más de una salvaguarda {efecto} efectiva en dimensión {dimension} para "
            f"{codigo_origen_amenaza} / {nombre_amenaza}: falta definir "
            "la regla de combinación de salvaguardas."
        )

    codigos_aplicables = {sg["codigo_salvaguarda"] for sg in matches}
    codigos_paquete = {m["codigo_salvaguarda"] for m in paquete["miembros"]}
    faltantes = codigos_aplicables - codigos_paquete
    if faltantes:
        raise ValueError(
            f"El paquete {paquete['nombre']!r} ({efecto}, dimensión {dimension}, "
            f"{codigo_origen_amenaza} / {nombre_amenaza}) no incluye todas las "
            f"salvaguardas aplicables: faltan {sorted(faltantes)}."
        )

    return paquete["eficacia_combinada"]


def propagar_e_calcular_impacto(activos, dep_edges, per_edges, amenazas_por_activo, valor_acumulado,
                                  salvaguardas_efectivas=None, paquetes_salvaguardas=None):
    """
    Calcula, para cada activo:
      - Directa: sus propias amenazas (grado 100%) — para TODOS los
        activos, tengan o no dependencias/personas asociadas.
      - Dependencia: amenazas heredadas transitivamente de lo que depende
        (solo activos que sí tienen dependencias hacia abajo).
      - Persona asociada: amenazas de Personal genéricas, heredadas con
        el grado de la relación (y el grado transitivo si es vía otro activo).

    amenazas_por_activo debe venir SIN FILTRAR (asignar_amenazas(..., filtrar=False))
    — el filtro se decide aquí, por cada fila, según el activo destino.

    salvaguardas_efectivas: {(codigo_activo_origen, nombre_amenaza): [ {efecto_calculo,
    ep_efectiva, ei_efectiva, aplica_d..aplica_t, ...}, ... ]} — ver
    app.cargar_salvaguardas_efectivas(). El "activo origen" de la amenaza NO es
    siempre el activo de la fila (cod_s): para amenazas heredadas (Dependencia,
    o Persona asociada vía dependencia) las salvaguardas que aplican son las del
    activo del que procede la amenaza (cod_d), no las del activo destino que
    recibe el riesgo repercutido — de ahí el parámetro explícito
    codigo_origen_amenaza en agregar().

    Devuelve una lista de filas (dict) con degradación, degradación
    repercutida e impacto (potencial y residual) ya calculados.
    """
    dep_adj_forward = construir_adj_forward(dep_edges)
    salvaguardas_efectivas = salvaguardas_efectivas or {}
    paquetes_salvaguardas = paquetes_salvaguardas or {}

    personas_por_activo = defaultdict(list)
    for act, persona_nombre, rol, grado in per_edges:
        personas_por_activo[act].append((persona_nombre, rol, grado))

    filas = []

    def agregar(cod_s, origen, detalle, grado, amenaza, usar_acumulado, codigo_origen_amenaza):
        s = activos[cod_s]
        tipo_s = s["tipo"]
        if _excluir_para_destino(tipo_s, amenaza):
            return
        va_s = valor_acumulado.get(cod_s, {})
        vp_s = s["valor_propio"]
        aro = ARO_POR_PROBABILIDAD.get(amenaza.get("probabilidad"), 0)
        fila = {"codigo": cod_s, "activo": s["nombre"], "tipo": tipo_s,
                "origen": origen, "detalle_origen": detalle,
                "grado_transitivo": round(grado, 1),
                "amenaza": amenaza["amenaza"], "categoria": amenaza.get("categoria"),
                "subcategoria": amenaza["subcategoria"], "probabilidad": amenaza["probabilidad"],
                "aro": aro}

        for dim in DIMENSIONES:
            deg = amenaza.get(dim)
            deg_rep = round(deg * (grado / 100.0), 2) if deg is not None else None
            deg_rep_m = _mask(tipo_s, dim, deg_rep)
            fila[f"{dim}"] = _mask(tipo_s, dim, deg)
            fila[f"{dim}_rep"] = deg_rep_m

            # --- EP/EI efectivas de las salvaguardas del activo origen de la
            # amenaza, para esta dimensión (una sola -> directa; varias ->
            # el paquete que las combine, ver resolver_eficacia()).
            ep = resolver_eficacia(codigo_origen_amenaza, amenaza["amenaza"], "EP", dim,
                                    salvaguardas_efectivas, paquetes_salvaguardas)
            ei = resolver_eficacia(codigo_origen_amenaza, amenaza["amenaza"], "EI", dim,
                                    salvaguardas_efectivas, paquetes_salvaguardas)
            fila[f"{dim}_ep_efectiva"] = ep
            fila[f"{dim}_ei_efectiva"] = ei

            # --- Degradación residual: la EI reduce la degradación (antes de
            # repercutir), luego se repercute igual que la potencial.
            degradacion_residual = round(deg * (1 - ei), 2) if deg is not None else None
            degradacion_residual_rep = (
                round(degradacion_residual * (grado / 100.0), 2)
                if degradacion_residual is not None else None
            )
            degradacion_residual_rep_m = _mask(tipo_s, dim, degradacion_residual_rep)
            fila[f"{dim}_degradacion_residual"] = _mask(tipo_s, dim, degradacion_residual)
            fila[f"{dim}_rep_residual"] = degradacion_residual_rep_m

            # --- ARO residual: la EP reduce el ARO.
            aro_residual = round(aro * (1 - ep), 4)
            fila[f"{dim}_aro_residual"] = aro_residual

            if deg_rep_m is None:
                fila[f"{dim}_impacto"] = None
                fila[f"{dim}_riesgo"] = None
                fila[f"{dim}_impacto_cualitativo"] = None
                fila[f"{dim}_riesgo_cualitativo"] = None
            else:
                valor = (va_s.get(dim) if usar_acumulado else vp_s.get(dim)) or 0.0
                # Cuantitativo (§6.4): multiplicación directa, sin cambios.
                impacto = round(valor * (deg_rep_m / 100.0), 2)
                fila[f"{dim}_impacto"] = impacto
                fila[f"{dim}_riesgo"] = round(impacto * aro, 4)
                # Cualitativo (§6.3): tabla de degradación para el Impacto (§2.5.1),
                # fórmula log-lineal para el Riesgo (§6.3.3/§6.3.4) — dos
                # pasos distintos a los del Cuantitativo, no una reutilización.
                impacto_cual = impacto_tabla(valor, deg_rep_m)
                fila[f"{dim}_impacto_cualitativo"] = impacto_cual
                fila[f"{dim}_riesgo_cualitativo"] = riesgo_cualitativo_formula(impacto_cual, aro)

            if degradacion_residual_rep_m is None:
                fila[f"{dim}_impacto_residual"] = None
                fila[f"{dim}_riesgo_residual"] = None
                fila[f"{dim}_impacto_residual_cualitativo"] = None
                fila[f"{dim}_riesgo_residual_cualitativo"] = None
            else:
                valor = (va_s.get(dim) if usar_acumulado else vp_s.get(dim)) or 0.0
                # Mismo motor que el potencial (§6.4), con la degradación
                # repercutida residual en vez de la potencial.
                impacto_residual = round(valor * (degradacion_residual_rep_m / 100.0), 2)
                fila[f"{dim}_impacto_residual"] = impacto_residual
                fila[f"{dim}_riesgo_residual"] = round(impacto_residual * aro_residual, 4)
                impacto_residual_cual = impacto_tabla(valor, degradacion_residual_rep_m)
                fila[f"{dim}_impacto_residual_cualitativo"] = impacto_residual_cual
                fila[f"{dim}_riesgo_residual_cualitativo"] = riesgo_cualitativo_formula(
                    impacto_residual_cual, aro_residual
                )
        filas.append(fila)

    # --- Directa: para TODOS los activos. Origen de la amenaza = el propio activo. ---
    for cod_s in activos:
        for am in amenazas_por_activo.get(cod_s, []):
            agregar(cod_s, "Directa", "(propia del activo)", 100, am, usar_acumulado=True,
                    codigo_origen_amenaza=cod_s)

    # --- Heredadas: solo activos con dependencias o personas asociadas ---
    sujetos_relacion = sorted(set(dep_adj_forward.keys()) | set(personas_por_activo.keys()))

    for cod_s in sujetos_relacion:
        # Persona asociada directamente a cod_s -> origen = cod_s (el activo
        # al que está asociada la persona).
        for persona_nombre, rol, grado_persona in personas_por_activo.get(cod_s, []):
            for am in amenazas_por_activo.get("__PERSONAL__", []):
                agregar(cod_s, "Persona asociada", f"{persona_nombre} ({rol}), grado {grado_persona}%",
                        grado_persona, am, usar_acumulado=False, codigo_origen_amenaza=cod_s)

        for cod_d, g in dependencias_transitivas(cod_s, dep_adj_forward).items():
            d = activos[cod_d]
            # Dependencia -> origen = cod_d (el activo inferior del que procede la amenaza).
            for am in amenazas_por_activo.get(cod_d, []):
                agregar(cod_s, "Dependencia", f"{cod_d} · {d['nombre']} ({d['tipo']})", g, am,
                        usar_acumulado=False, codigo_origen_amenaza=cod_d)
            # Persona asociada vía dependencia -> origen = cod_d (el activo
            # inferior donde está asociada la persona), no cod_s.
            for persona_nombre, rol, grado_persona in personas_por_activo.get(cod_d, []):
                g_efectivo = g * (grado_persona / 100.0)
                for am in amenazas_por_activo.get("__PERSONAL__", []):
                    agregar(cod_s, "Persona asociada",
                            f"{persona_nombre} ({rol}), grado {grado_persona}% — vía {cod_d} · {d['nombre']}",
                            g_efectivo, am, usar_acumulado=False, codigo_origen_amenaza=cod_d)

    return filas


def impacto_maximo_por_activo(filas):
    """
    Para la vista resumen de Impacto: por cada activo, la fila y
    dimensión con mayor impacto (ignorando las dimensiones sin valor).
    Devuelve {codigo: {impacto, dimension, amenaza, origen, detalle_origen}}.
    """
    resumen = {}
    for f in filas:
        for dim in DIMENSIONES:
            val = f.get(f"{dim}_impacto")
            if val is None:
                continue
            actual = resumen.get(f["codigo"])
            if actual is None or val > actual["impacto"]:
                resumen[f["codigo"]] = {
                    "codigo": f["codigo"], "nombre": f["activo"], "tipo": f["tipo"],
                    "impacto": val, "dimension": dim, "amenaza": f["amenaza"],
                    "origen": f["origen"], "detalle_origen": f["detalle_origen"],
                }
    return resumen


def riesgo_maximo_por_activo(filas):
    """
    Igual que impacto_maximo_por_activo pero para Riesgo (Impacto x ARO).
    Devuelve {codigo: {riesgo, dimension, amenaza, origen, detalle_origen}}.
    """
    resumen = {}
    for f in filas:
        for dim in DIMENSIONES:
            val = f.get(f"{dim}_riesgo")
            if val is None:
                continue
            actual = resumen.get(f["codigo"])
            if actual is None or val > actual["riesgo"]:
                resumen[f["codigo"]] = {
                    "codigo": f["codigo"], "nombre": f["activo"], "tipo": f["tipo"],
                    "riesgo": val, "dimension": dim, "amenaza": f["amenaza"],
                    "origen": f["origen"], "detalle_origen": f["detalle_origen"],
                }
    return resumen


def matriz_por_activo(filas, sufijo_campo):
    """
    Para cada activo, el máximo de '{dim}_{sufijo_campo}' en CADA
    dimensión por separado (D, I, C, A, T) — a diferencia de
    impacto_maximo_por_activo/riesgo_maximo_por_activo, que colapsan
    las 5 dimensiones en un único valor. Pensada para la tabla resumen
    con una columna por dimensión.

    Devuelve {codigo: {D: valor_o_None, I: ..., C: ..., A: ..., T: ...}}.
    """
    resultado = {}
    for f in filas:
        cod = f["codigo"]
        fila_activo = resultado.setdefault(cod, {d: None for d in DIMENSIONES})
        for dim in DIMENSIONES:
            val = f.get(f"{dim}_{sufijo_campo}")
            if val is None:
                continue
            actual = fila_activo[dim]
            if actual is None or val > actual:
                fila_activo[dim] = val
    return resultado


def impacto_cualitativo_maximo_por_activo(filas):
    """
    Igual que impacto_maximo_por_activo, pero sobre '{dim}_impacto_cualitativo'
    (tabla de degradación, §2.5.1) — comparación numérica normal, ya no por
    nivel ordinal, desde que el Cualitativo pasó a dar un número 0-10.
    """
    resumen = {}
    for f in filas:
        for dim in DIMENSIONES:
            val = f.get(f"{dim}_impacto_cualitativo")
            if val is None:
                continue
            actual = resumen.get(f["codigo"])
            if actual is None or val > actual["impacto"]:
                resumen[f["codigo"]] = {
                    "codigo": f["codigo"], "nombre": f["activo"], "tipo": f["tipo"],
                    "impacto": val, "dimension": dim, "amenaza": f["amenaza"],
                    "origen": f["origen"], "detalle_origen": f["detalle_origen"],
                }
    return resumen


def riesgo_cualitativo_maximo_por_activo(filas):
    """Igual que riesgo_maximo_por_activo, pero sobre '{dim}_riesgo_cualitativo'
    (fórmula log-lineal, §6.3.3/§6.3.4)."""
    resumen = {}
    for f in filas:
        for dim in DIMENSIONES:
            val = f.get(f"{dim}_riesgo_cualitativo")
            if val is None:
                continue
            actual = resumen.get(f["codigo"])
            if actual is None or val > actual["riesgo"]:
                resumen[f["codigo"]] = {
                    "codigo": f["codigo"], "nombre": f["activo"], "tipo": f["tipo"],
                    "riesgo": val, "dimension": dim, "amenaza": f["amenaza"],
                    "origen": f["origen"], "detalle_origen": f["detalle_origen"],
                }
    return resumen


def ejecutar_recalculo(activos, dep_edges, per_edges, catalogo, salvaguardas_efectivas=None,
                        paquetes_salvaguardas=None):
    """
    Punto de entrada único que usa la app: dado todo lo leído de Supabase,
    devuelve (valor_acumulado, amenazas_directas_por_activo, filas_propagacion).
    filas_propagacion incluye ahora filas "Directa" para TODOS los activos,
    no solo los que tienen dependencias/personas asociadas.

    salvaguardas_efectivas (opcional): ver propagar_e_calcular_impacto() y
    app.cargar_salvaguardas_efectivas(). Si no se pasa, el cálculo residual
    coincide con el potencial (ninguna salvaguarda aplicada).

    paquetes_salvaguardas (opcional): ver propagar_e_calcular_impacto() /
    resolver_eficacia() y app.cargar_paquetes_salvaguardas(). Solo hace
    falta cuando, para una misma amenaza+dimensión+efecto, hay más de una
    salvaguarda efectiva y su combinación no está definida por defecto.
    """
    valor_acumulado = calcular_valor_acumulado(activos, dep_edges, per_edges)

    amenazas_sin_filtrar = asignar_amenazas(activos, catalogo, filtrar=False)
    # amenazas genericas de Personal, para la propagacion via persona (por rol, no por nombre)
    amenazas_sin_filtrar["__PERSONAL__"] = [
        am for am in catalogo if "PERSONAL" in am["tipos_activo"] and not am["subcategoria"]
    ]

    amenazas_directas = asignar_amenazas(activos, catalogo, filtrar=True)

    filas_propagacion = propagar_e_calcular_impacto(
        activos, dep_edges, per_edges, amenazas_sin_filtrar, valor_acumulado,
        salvaguardas_efectivas=salvaguardas_efectivas,
        paquetes_salvaguardas=paquetes_salvaguardas,
    )

    return valor_acumulado, amenazas_directas, filas_propagacion
