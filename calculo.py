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

Cambios v5 (08/09/2026) — reescritura desde Metodologia_App_Riesgos_1.md
(version actualizada del responsable de area), IMPLEMENTADA DIRECTAMENTE
DESDE LA ESPECIFICACION (sin archivo .py de referencia disponible).
Sustituye por completo el enfoque de letras/matriz de v4 (ver mas abajo,
DESACTUALIZADO -- nivel_impacto_para_riesgo(), nivel_riesgo_cualitativo()
antiguo y MATRIZ_RIESGO ya NO EXISTEN, eliminadas en este cambio):
  - dependencias_transitivas(): combina caminos "diamante" con la suma
    de Bayes (a+b-axb), no con el maximo -- Libro III §2.2.2. Verificado
    con el ejemplo exacto del documento (0,92, no 0,80).
  - impacto_tabla(): metodo Cualitativo (§2.5.1), tabla de degradacion
    mas generosa que "valor x degradacion" a partir del 50%.
  - riesgo_cualitativo(): formula log-lineal
    max(0,01; 0,771xImpacto + 1,147xlog10(ARO)), acotada a 0-10,
    cumple los axiomas de Magerit §2.2.1 (ratio Impacto:Probabilidad
    1,5:1). Campos {dim}_impacto_cualitativo / {dim}_riesgo_cualitativo.
  - riesgo_cuantitativo(): Impacto x ARO_ajustado, SIN NINGUNA
    PROTECCION (a proposito, es lo que lo distingue del Cualitativo).
    Campos {dim}_impacto / {dim}_riesgo (se mantiene el nombre de v4,
    pero ya no lleva el tope de 100 que v4 le ponia).
  - maximo_por_activo(filas, campo) sustituye a impacto_maximo_por_activo()
    y riesgo_maximo_por_activo() (v4) -- generica para los 4 campos.
  - Los 6 ejemplos numericos exactos del documento verificados uno a
    uno antes de dar el cambio por bueno.

Cambios v4 (08/09/2026) -- DESACTUALIZADO, sustituido por completo en v5.
Se conserva aqui solo como historial de por que existio la v3 (letras
Magerit) y por que se abandono:
Metodologia_App_Riesgos.md (responsable de área) + verificación directa
contra Magerit Libro III, página 7 (LibroIIIGuiadeTecnicas.pdf):
  - v3 mezclaba el método Cualitativo y el Cuantitativo de Magerit
    (Libro III §2.2.1/§2.1 vs §2.2.2), que son técnicas SEPARADAS y no
    se deben combinar: v3 calculaba "Impacto + log10(ARO)" y de ahí
    derivaba hacia atrás una probabilidad cualitativa
    (probabilidad_desde_aro(), ELIMINADA) para meterla en la tabla de
    Magerit -- eso es exactamente la confusión que señaló el
    responsable de área.
  - MATRIZ_RIESGO tenía además varias celdas incorrectas (se había
    construido a partir de una búsqueda web, no del PDF real) --
    corregida y verificada visualmente contra la página 7 del libro.
  - Ahora se calculan los dos métodos por separado, cada fila:
    - Cualitativo ({dim}_riesgo_nivel): tabla Impacto(nivel)×Probabilidad
      (cruda del catálogo, NUNCA convertida a ARO) → uno de 5 niveles
      MB/B/M/A/MA, sin matiz, sin número.
    - Cuantitativo ({dim}_riesgo): Impacto × ARO_ajustado (FS×FE del
      Factor de Riesgo Contextual incluido aquí, nunca en el
      Cualitativo), sin logaritmo, número real sin acotar (salvo el
      tope de 100 ya existente en el ARO ajustado).
  - Ver nivel_impacto_para_riesgo(), nivel_riesgo_cualitativo(),
    calcular_riesgo_cuantitativo().

Cambios v3 (07/09/2026):
  - Niveles cualitativos Magerit (0/MB/B/M/A/MA + matiz -/+) para
    Impacto, corrección de mapeo subtipo->subcategoria (DOMINIO,
    FIREWALL, HYPERVISOR).
  - calcular_valor_acumulado(): cambia de SUMA a MAXIMO entre valor
    propio y lo heredado (ya ponderado por grado) de cada superior.
    Se APARTA deliberadamente de "la misma logica validada en el
    modelo de Excel" que dice el parrafo de arriba -- ese Excel no se
    ha podido localizar ni confirmar en esta sesion (ORIGEN NO
    DETERMINADO). Detalle y justificacion en el comentario dentro de
    calcular_valor_acumulado().

Cambios v2 (soporte a las páginas Amenazas / Impacto separadas y al
árbol de dependencias en la página Dependencias):
  - dependencias_transitivas() y construir_adj_forward() ahora son
    funciones de módulo reutilizables (antes vivían anidadas dentro de
    propagar_e_calcular_impacto), para poder pintar el árbol desde app.py.
  - Las filas "Directa" se calculan ahora para TODOS los activos, no
    solo para los que tienen dependencias o personas asociadas — así
    la página de Impacto puede mostrar el impacto máximo de cualquier
    activo, tenga o no relaciones.
  - Nueva función impacto_maximo_por_activo() para la vista tipo PILAR.
"""

from collections import defaultdict, deque
from math import log10

DIMENSIONES = ["D", "I", "C", "A", "T"]

# ARO (tasa anual de ocurrencia) por nivel de Probabilidad. Magerit 3.0,
# Libro I §3.1.2, Tabla 2 -- misma tabla que ya usa el Factor de Riesgo
# Contextual. Entra en los dos metodos (Cualitativo y Cuantitativo,
# Metodologia_App_Riesgos.md §6.1) -- la diferencia entre ambos NO es
# "quien usa ARO", es como lo usa cada uno (ver riesgo_cualitativo/
# riesgo_cuantitativo mas abajo).
PROBABILIDAD_ARO = {
    "Muy Alta": 100, "Alta": 10, "Media": 1, "Baja": 0.1, "Muy Baja": 0.01,
}

# Tabla de Impacto -- metodo Cualitativo (Metodologia_App_Riesgos.md
# §2.5.1). Mas generosa que "valor x degradacion" a partir del 50% de
# degradacion -- protege el Impacto de caer proporcionalmente igual de
# rapido que la degradacion. Filas = degradacion (redondeada a la mas
# cercana), columnas = valor del activo (redondeado al entero 1-10).
# 90% y 100% dan la tabla identica (ambas "Total o casi total").
TABLA_IMPACTO_CUALITATIVO = {
    100: {1: 1, 2: 2, 3: 3, 4: 4, 5: 5, 6: 6, 7: 7, 8: 8, 9: 9, 10: 10},
    90:  {1: 1, 2: 2, 3: 3, 4: 4, 5: 5, 6: 6, 7: 7, 8: 8, 9: 9, 10: 10},
    50:  {1: 0, 2: 1, 3: 2, 4: 3, 5: 4, 6: 5, 7: 6, 8: 7, 9: 8, 10: 9},
    25:  {1: 0, 2: 0, 3: 1, 4: 2, 5: 3, 6: 4, 7: 5, 8: 6, 9: 7, 10: 8},
    10:  {1: 0, 2: 0, 3: 0, 4: 1, 5: 2, 6: 3, 7: 4, 8: 5, 9: 6, 10: 7},
    1:   {1: 0, 2: 0, 3: 0, 4: 0, 5: 0, 6: 0, 7: 1, 8: 2, 9: 3, 10: 4},
}


def _fila_degradacion(deg):
    """Redondea una degradacion continua (0-100) a la fila mas cercana
    de TABLA_IMPACTO_CUALITATIVO, usando el punto medio entre filas
    consecutivas como corte (§2.5.1): <=5,5%->1%, <=17,5%->10%,
    <=37,5%->25%, <=70%->50%, >70%->90% (identica a 100%)."""
    if deg <= 5.5:
        return 1
    if deg <= 17.5:
        return 10
    if deg <= 37.5:
        return 25
    if deg <= 70:
        return 50
    return 90


def impacto_tabla(valor, degradacion):
    """
    Metodo Cualitativo (§2.5.1): Impacto = tabla_degradacion(valor,
    degradacion), NO valor x degradacion (esa es la formula del
    Cuantitativo, ver mas abajo). valor se redondea al entero 1-10 mas
    cercano para poder indexar la tabla; valor<=0 o degradacion None/0
    da Impacto=0 directamente (ℜ(0,·)=0, axioma de Magerit §2.2.1).
    """
    if valor is None or valor <= 0 or degradacion is None:
        return 0
    v = round(valor)
    if v <= 0:
        return 0
    v = min(v, 10)
    fila = _fila_degradacion(degradacion)
    return TABLA_IMPACTO_CUALITATIVO[fila][v]


def riesgo_cualitativo_desde_aro(impacto, aro):
    """
    Nucleo de la formula log-lineal (§6.3.3/6.3.4), separado de
    riesgo_cualitativo() para poder reutilizarlo con un ARO ya reducido
    por salvaguardas (ARO residual, ver riesgo_actual_cualitativo), sin
    tener que pasar por una Probabilidad-etiqueta. Impacto=0 -> Riesgo=0
    exacto. Acotado a 0-10 (extension nuestra, no del documento origen).
    """
    if impacto is None or impacto == 0:
        return 0
    if aro is None or aro <= 0:
        return 0
    riesgo = max(0.01, 0.771 * impacto + 1.147 * log10(aro))
    return round(min(riesgo, 10), 2)


def riesgo_cualitativo(impacto, probabilidad, fs=1.0, fe=1.0):
    """
    Metodo Cualitativo (§6.3.3/6.3.4): formula log-lineal ajustada para
    cumplir los axiomas de Magerit Libro III §2.2.1 (ℜ(0,p)=0; creciente
    en valor y en probabilidad; mas peso al impacto que a la
    probabilidad -- ratio 1,5:1). El Factor de Riesgo Contextual
    (FS x FE) se aplica sobre el ARO antes de entrar en la formula.
    """
    aro = PROBABILIDAD_ARO.get(probabilidad)
    if aro is None:
        return None
    aro_ajustado = aro * (fs or 1.0) * (fe or 1.0)
    return riesgo_cualitativo_desde_aro(impacto, aro_ajustado)


def riesgo_cuantitativo(impacto, probabilidad, fs=1.0, fe=1.0):
    """
    Metodo Cuantitativo (§6.4.3/6.4.4): Riesgo = Impacto x ARO,
    multiplicacion directa, SIN NINGUNA PROTECCION -- esta es
    precisamente la caracteristica que lo distingue del Cualitativo
    (§6, tabla comparativa), no un descuido. Numero real, sin acotar
    (el Factor de Riesgo Contextual FS x FE se aplica sobre el ARO
    igual que en el Cualitativo, §6.5).
    """
    if impacto is None:
        return None
    aro = PROBABILIDAD_ARO.get(probabilidad)
    if aro is None:
        return None
    aro_ajustado = aro * (fs or 1.0) * (fe or 1.0)
    return round(impacto * aro_ajustado, 2)


def combinar_eficacias(lista_eficacias):
    """
    Combina varias salvaguardas sobre la misma amenaza con la suma de
    Bayes (a+b-axb), igual que ya hacemos en dependencias_transitivas()
    para caminos "diamante". DECISION DE PROYECTO (no es la formula de
    "escalones" de Magerit, pensada para PILAR; confirmada con Cris el
    08/09/2026): es la forma estandar de combinar "probabilidad de que
    al menos una funcione" para medidas independientes.
    lista_eficacias: [(ei, ep), ...] en porcentaje 0-100 cada una.
    Devuelve (ei_combinado, ep_combinado), tambien en 0-100.
    """
    ei_c, ep_c = 0.0, 0.0
    for ei, ep in lista_eficacias:
        a = (ei or 0.0) / 100.0
        ei_c = ei_c + a - ei_c * a
        b = (ep or 0.0) / 100.0
        ep_c = ep_c + b - ep_c * b
    return round(ei_c * 100, 2), round(ep_c * 100, 2)


def degradacion_residual(degradacion, ei):
    """
    NO SE USA en el motor desde el 09/09/2026 -- se dejaba esta funcion
    para reducir la degradacion y volver a pasarla por impacto_tabla(),
    pero Magerit da un ejemplo numerico literal distinto (Libro III):
    reduce el IMPACTO directamente, "impacto_residual = impacto x (1-ei)",
    no la degradacion. Ambas formulas cumplen los mismos axiomas en los
    extremos (dr(0,ei)=0; dr(d,0)=d; dr(d,1)=0), pero dan resultados
    distintos en valores intermedios -- se detecto la discrepancia
    (responsable de area) comparando un caso de prueba: esta funcion
    daba Riesgo=3,98, la formula literal de Magerit da Riesgo=1,70.
    Se mantiene aqui solo como referencia, no la llama nadie.
    """
    if degradacion is None:
        return None
    return degradacion * (1 - (ei or 0.0) / 100.0)


def aro_residual(aro_ajustado, ep):
    """
    Magerit Libro III, "Analisis algoritmico": pr(0,ep)=0; pr(p,0)=p;
    pr(p,1)=0 -- misma logica que la degradacion residual, aplicada al
    ARO ya ajustado por el Factor de Riesgo Contextual (FS x FE).
    """
    if aro_ajustado is None:
        return None
    return aro_ajustado * (1 - (ep or 0.0) / 100.0)

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
    "CIBERSEGURIDAD": "Seguridad", "DOMINIO": "Dominio",
    "DESARROLLO": None, "FIREWALL": "Firewall",
    "MAQUINA_VIRTUAL": "Maquina Virtual", "HYPERVISOR": "Hipervisor",
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

    Devuelve {codigo: {D,I,C,A,T}} con el valor acumulado por dimensión,
    y lanza ValueError si detecta un ciclo en el grafo de dependencias.
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

    # DECISION (confirmada con Cris el 07/09/2026, tras detectar que el
    # acumulado por SUMA podia superar la escala 0-10 de Magerit -- ej.
    # un activo con varios superiores dependiendo de el llegaba a 13.28,
    # fuera de la escala que usa PILAR (0-9.9, Tabla 5 "Niveles de
    # riesgo")). Se cambia a MAXIMO: el valor acumulado de un activo es
    # el mayor entre su propio valor y lo heredado (ya ponderado por
    # grado) de cada superior -- no la suma de todos ellos. Esto se
    # aparta de la formula que el modelo de Excel original tenia
    # validada segun el docstring de este archivo (ORIGEN NO
    # DETERMINADO -- no se ha podido localizar ni confirmar ese Excel
    # en esta sesion), asi que queda documentado aqui como cambio
    # metodologico explicito, no como correccion de un error.
    valor_acumulado = {}
    for cod in orden:
        propio = activos[cod]["valor_propio"]
        va = {}
        for dim in DIMENSIONES:
            valores = [propio.get(dim) or 0.0]
            for sup, grado in adj.get(cod, []):
                heredado = (valor_acumulado.get(sup, {}).get(dim) or 0.0) * (grado / 100.0)
                valores.append(heredado)
            va[dim] = round(max(valores), 2)
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
    Devuelve {inferior: grado_transitivo} -- todo lo alcanzable hacia
    abajo desde 'origen'. Cuando hay MAS DE UN CAMINO hasta el mismo
    activo inferior (estructuras de diamante), Magerit exige combinar
    los caminos, no quedarse con el de mayor grado (Libro III §2.2.2,
    "Las dependencias entre activos"): grado(A=>C) = suma-de-Bayes de
    cada camino, a+b = 1-(1-a)x(1-b) -- para que la combinacion nunca
    supere el 100% por muchos caminos que existan.
    DECISION/CORRECCION (Metodologia_App_Riesgos.md §2.1, responsable
    de area, 08/09/2026): una version anterior de este proyecto se
    quedaba con el camino de mayor grado; queda corregido aqui.

    Requiere que el grafo sea acíclico (ya se valida en otro punto del
    modulo, calcular_valor_acumulado). Se resuelve con un orden
    topologico del subgrafo alcanzable desde origen, para poder sumar
    TODAS las contribuciones que lleguen a cada nodo antes de darlo
    por definitivo.
    """
    alcanzables = {origen}
    pila = [origen]
    while pila:
        n = pila.pop()
        for inf, _ in dep_adj_forward.get(n, []):
            if inf not in alcanzables:
                alcanzables.add(inf)
                pila.append(inf)

    entrantes = defaultdict(list)   # nodo -> [(predecesor, grado_arista)]
    grado_restante = {n: 0 for n in alcanzables}
    for sup in alcanzables:
        for inf, grado in dep_adj_forward.get(sup, []):
            if inf in alcanzables:
                entrantes[inf].append((sup, grado))
                grado_restante[inf] += 1

    orden = []
    cola = deque(n for n in alcanzables if grado_restante[n] == 0)
    while cola:
        n = cola.popleft()
        orden.append(n)
        for inf, _ in dep_adj_forward.get(n, []):
            if inf in alcanzables:
                grado_restante[inf] -= 1
                if grado_restante[inf] == 0:
                    cola.append(inf)

    grado_acumulado = {origen: 1.0}   # fraccion 0-1, origen = 100%
    for n in orden:
        if n == origen:
            continue
        combinado = 0.0
        for pred, g_arista in entrantes[n]:
            aporte = grado_acumulado.get(pred, 0.0) * (g_arista / 100.0)
            combinado = combinado + aporte - combinado * aporte   # suma de Bayes
        grado_acumulado[n] = combinado

    return {n: round(grado_acumulado[n] * 100.0, 2) for n in alcanzables if n != origen}


def propagar_e_calcular_impacto(activos, dep_edges, per_edges, amenazas_por_activo, valor_acumulado,
                                 eficacias_por_activo_categoria=None):
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

    eficacias_por_activo_categoria: {(codigo_activo, categoria): [(ei,ep), ...]}
    -- las salvaguardas se buscan SIEMPRE en el activo donde la amenaza
    vive de verdad (el origen), no en quien la hereda -- asi la
    proteccion se propaga automaticamente por la cadena de dependencia,
    igual que ya se propaga el propio riesgo (confirmado con Cris el
    08/09/2026). Si no se pasa (None), el Riesgo Actual sale igual al
    Potencial (ninguna salvaguarda encontrada).

    Devuelve una lista de filas (dict) con degradación, degradación
    repercutida e impacto ya calculados.
    """
    eficacias_por_activo_categoria = eficacias_por_activo_categoria or {}
    dep_adj_forward = construir_adj_forward(dep_edges)

    personas_por_activo = defaultdict(list)
    for act, persona_nombre, rol, grado in per_edges:
        personas_por_activo[act].append((persona_nombre, rol, grado))

    filas = []

    def agregar(cod_s, origen, detalle, grado, amenaza, usar_acumulado, origen_salvaguarda):
        s = activos[cod_s]
        tipo_s = s["tipo"]
        if _excluir_para_destino(tipo_s, amenaza):
            return
        va_s = valor_acumulado.get(cod_s, {})
        vp_s = s["valor_propio"]
        fila = {"codigo": cod_s, "activo": s["nombre"], "tipo": tipo_s,
                "origen": origen, "detalle_origen": detalle,
                "grado_transitivo": round(grado, 1),
                "amenaza": amenaza["amenaza"], "categoria": amenaza.get("categoria"),
                "subcategoria": amenaza["subcategoria"], "probabilidad": amenaza["probabilidad"]}

        lista_efic = eficacias_por_activo_categoria.get((origen_salvaguarda, amenaza.get("categoria")), [])
        ei_c, ep_c = combinar_eficacias(lista_efic) if lista_efic else (0.0, 0.0)

        for dim in DIMENSIONES:
            deg = amenaza.get(dim)
            deg_rep = round(deg * (grado / 100.0), 2) if deg is not None else None
            deg_rep_m = _mask(tipo_s, dim, deg_rep)
            fila[f"{dim}"] = _mask(tipo_s, dim, deg)
            fila[f"{dim}_rep"] = deg_rep_m
            if deg_rep_m is None:
                fila[f"{dim}_impacto"] = None
                fila[f"{dim}_riesgo"] = None
                fila[f"{dim}_impacto_cualitativo"] = None
                fila[f"{dim}_riesgo_cualitativo"] = None
                fila[f"{dim}_impacto_cualitativo_actual"] = None
                fila[f"{dim}_riesgo_cualitativo_actual"] = None
            else:
                valor = (va_s.get(dim) if usar_acumulado else vp_s.get(dim)) or 0.0
                fs = amenaza.get("fs") if amenaza.get("fs") is not None else 1.0
                fe = amenaza.get("fe") if amenaza.get("fe") is not None else 1.0

                # Cuantitativo (§6.4): Impacto = valor x degradacion,
                # Riesgo = Impacto x ARO, sin ninguna proteccion.
                impacto_cuant = round(valor * (deg_rep_m / 100.0), 2)
                fila[f"{dim}_impacto"] = impacto_cuant
                fila[f"{dim}_riesgo"] = riesgo_cuantitativo(
                    impacto_cuant, amenaza["probabilidad"], fs, fe
                )

                # Cualitativo POTENCIAL (§6.3): sin salvaguardas.
                impacto_cual = impacto_tabla(valor, deg_rep_m)
                fila[f"{dim}_impacto_cualitativo"] = impacto_cual
                fila[f"{dim}_riesgo_cualitativo"] = riesgo_cualitativo(
                    impacto_cual, amenaza["probabilidad"], fs, fe
                )

                # Cualitativo ACTUAL (Riesgo Residual): con salvaguardas
                # del activo ORIGEN de la amenaza (ei_c/ep_c ya combinadas).
                # CORREGIDO 09/09/2026: Magerit reduce el IMPACTO directamente
                # -- "impacto_residual = impacto x (1-ei)" (Libro III, ejemplo
                # numerico literal) -- no la degradacion para luego volver a
                # pasarla por la tabla (esa era la version anterior, incorrecta:
                # ambas cumplen los mismos axiomas en los extremos 0%/100%, pero
                # dan resultados distintos en los valores intermedios).
                impacto_cual_actual = round(impacto_cual * (1 - ei_c / 100.0), 2)
                fila[f"{dim}_impacto_cualitativo_actual"] = impacto_cual_actual
                aro_base = PROBABILIDAD_ARO.get(amenaza["probabilidad"])
                aro_ajustado = aro_base * fs * fe if aro_base is not None else None
                aro_res = aro_residual(aro_ajustado, ep_c)
                fila[f"{dim}_riesgo_cualitativo_actual"] = riesgo_cualitativo_desde_aro(
                    impacto_cual_actual, aro_res
                )
        filas.append(fila)

    # --- Directa: para TODOS los activos ---
    for cod_s in activos:
        for am in amenazas_por_activo.get(cod_s, []):
            agregar(cod_s, "Directa", "(propia del activo)", 100, am,
                    usar_acumulado=True, origen_salvaguarda=cod_s)

    # --- Heredadas: solo activos con dependencias o personas asociadas ---
    sujetos_relacion = sorted(set(dep_adj_forward.keys()) | set(personas_por_activo.keys()))

    for cod_s in sujetos_relacion:
        for persona_nombre, rol, grado_persona in personas_por_activo.get(cod_s, []):
            for am in amenazas_por_activo.get("__PERSONAL__", []):
                agregar(cod_s, "Persona asociada", f"{persona_nombre} ({rol}), grado {grado_persona}%",
                        grado_persona, am, usar_acumulado=False, origen_salvaguarda=cod_s)

        for cod_d, g in dependencias_transitivas(cod_s, dep_adj_forward).items():
            d = activos[cod_d]
            for am in amenazas_por_activo.get(cod_d, []):
                agregar(cod_s, "Dependencia", f"{cod_d} · {d['nombre']} ({d['tipo']})", g, am,
                        usar_acumulado=False, origen_salvaguarda=cod_d)
            for persona_nombre, rol, grado_persona in personas_por_activo.get(cod_d, []):
                g_efectivo = g * (grado_persona / 100.0)
                for am in amenazas_por_activo.get("__PERSONAL__", []):
                    agregar(cod_s, "Persona asociada",
                            f"{persona_nombre} ({rol}), grado {grado_persona}% — vía {cod_d} · {d['nombre']}",
                            g_efectivo, am, usar_acumulado=False, origen_salvaguarda=cod_d)

    return filas


def maximo_por_activo(filas, campo):
    """
    Para la vista tipo PILAR: por cada activo, la fila y dimensión con
    mayor valor de `campo` (ignorando las dimensiones sin valor).
    `campo` es cualquiera de los 4 calculados en agregar():
    "impacto", "riesgo" (Cuantitativo) o "impacto_cualitativo",
    "riesgo_cualitativo" (Cualitativo). Nota: el maximo de un campo
    puede NO coincidir con el de otro -- una amenaza de impacto menor
    pero probabilidad mas alta puede tener mas riesgo. Por eso se
    calcula cada uno por separado, nunca reutilizando el resultado de
    otro campo.
    Devuelve {codigo: {valor, dimension, amenaza, probabilidad, origen,
    detalle_origen}}.
    """
    resumen = {}
    for f in filas:
        for dim in DIMENSIONES:
            val = f.get(f"{dim}_{campo}")
            if val is None:
                continue
            actual = resumen.get(f["codigo"])
            if actual is None or val > actual["valor"]:
                resumen[f["codigo"]] = {
                    "codigo": f["codigo"], "nombre": f["activo"], "tipo": f["tipo"],
                    "valor": val, "dimension": dim, "amenaza": f["amenaza"],
                    "probabilidad": f["probabilidad"],
                    "origen": f["origen"], "detalle_origen": f["detalle_origen"],
                }
    return resumen


def ejecutar_recalculo(activos, dep_edges, per_edges, catalogo, eficacias_por_activo_categoria=None):
    """
    Punto de entrada único que usa la app: dado todo lo leído de Supabase,
    devuelve (valor_acumulado, amenazas_directas_por_activo, filas_propagacion).
    filas_propagacion incluye ahora filas "Directa" para TODOS los activos,
    no solo los que tienen dependencias/personas asociadas.

    eficacias_por_activo_categoria: ver propagar_e_calcular_impacto().
    Opcional -- si no se pasa, el Riesgo Actual sale igual al Potencial
    (ninguna salvaguarda aplicada), sin romper llamadas antiguas.
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
        eficacias_por_activo_categoria,
    )

    return valor_acumulado, amenazas_directas, filas_propagacion
