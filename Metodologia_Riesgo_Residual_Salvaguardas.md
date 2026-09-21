# Metodología — Riesgo Residual y Salvaguardas

**Complementa a:** `Metodologia_App_Riesgos.md`, que en su apartado de pendientes indicaba explícitamente: *"Riesgo residual: no implementado. Sería el paso siguiente de Magerit — el Riesgo actual asume que no hay salvaguardas, o no descuenta su eficacia."* Este documento cierra ese punto.

**Fecha:** 08/09/2026 (actualizado el 08/09/2026 con el multiplicador de madurez)
**Estado:** Implementado y verificado end-to-end contra datos reales del proyecto.

---

## 1. Fundamento (Magerit 3.0, Libro I y Libro III)

### 1.1 — Las salvaguardas tienen dos eficacias independientes

Magerit (Libro I, §3.1.7) distingue dos tipos de eficacia, no una:

- **`ei`** — eficacia frente al **Impacto** (reduce la degradación)
- **`ep`** — eficacia frente a la **Probabilidad** (reduce el ARO)

> *"La salvaguarda ideal es 100% eficaz, eficacia que combina 2 factores [...] descomponer en una eficacia frente al impacto, ei, y una eficacia frente a la probabilidad, ep."* — Libro III, "Análisis algorítmico"

Ambas van de 0% (inútil) a 100% (perfecta).

### 1.2 — Diez tipos de salvaguarda, tres familias de efecto (Libro I, Tabla 3)

| Familia de efecto | Tipos | Qué reduce | Estado en este proyecto |
|---|---|---|---|
| Preventivas | `PR` preventiva, `DR` disuasoria, `EL` eliminatoria | Solo Probabilidad (`ep`) | ✅ Activo |
| Acotan la degradación | `IM` minimizadora, `CR` correctiva, `RC` recuperativa | Solo Impacto (`ei`) | ✅ Activo |
| Consolidan el efecto de las demás | `MN` monitorización, `DC` detección, `AW` concienciación, `AD` administrativas | Ninguna directamente — refuerzan la fiabilidad de las otras | ✅ Activo (eficacia directa provisional, ver §5) |

### 1.3 — Fórmulas de reducción (cita literal, Libro III "Análisis algorítmico")

```
dr(0, ei) = 0        dr(d, 0) = d        dr(d, 1) = 0
pr(0, ep) = 0        pr(p, 0) = p        pr(p, 1) = 0
```

> **Corrección (09/09/2026):** la traducción a variables continuas que aparecía aquí en la versión anterior del documento (`degradación_residual = degradación × (1-ei)`, seguido de volver a pasar por `impacto_tabla()`) **no es la fórmula que usa Magerit** — es una interpolación nuestra que solo coincide con Magerit en los extremos (0%/100% de eficacia), pero diverge en los valores intermedios. El responsable de área detectó la discrepancia comparando un caso de prueba (`Riesgo Actual` daba 3,98 en vez de 1,70). Corregido con la fórmula literal, ver más abajo.

Magerit da el ejemplo numérico exacto (Libro III, "Análisis algorítmico"):

> *"Si las salvaguardas tienen un 90% de eficacia sobre el impacto, el impacto residual es: impacto residual = 900.000 × (1 – 0,9) = 90.000"*

Es decir, se reduce el **Impacto directamente**, no la degradación:

```
Impacto_residual = Impacto_potencial × (1 − ei)
ARO_residual      = ARO_ajustado × (1 − ep)
```

(la fórmula del `ARO_residual` ya estaba bien desde el principio — coincide con el ejemplo literal de Magerit para la frecuencia: *"frecuencia residual = frecuencia × (1-ef)"*)

Donde `ARO_ajustado` es el mismo que ya usa el modelo Potencial (incluye el Factor de Riesgo Contextual `FS × FE`, ver `Metodologia_App_Riesgos.md` §6.5) — las salvaguardas actúan **después** de aplicar el contexto sectorial, no lo sustituyen.

### 1.4 — Impacto y Riesgo residuales (Libro III)

```
Impacto residual = Impacto_potencial × (1 − ei)
Riesgo residual  = riesgo_cualitativo_desde_aro(Impacto_residual, ARO_residual)
```

Se reutilizan **exactamente** las mismas funciones que ya calculan el modelo Potencial (`impacto_tabla()`, la fórmula log-lineal `max(0,01; 0,771×Impacto + 1,147×log₁₀(ARO))`) — el modelo Residual no es un motor distinto, es el mismo motor alimentado con degradación y ARO ya reducidos.

> *"El impacto acumulado residual se calcula sobre el valor acumulado. El impacto residual repercutido se calcula sobre el valor propio."* — Libro III

Es decir: el Residual respeta la misma distinción **Acumulado / Repercutido** que ya existe para el Potencial — no son cálculos aparte, son el mismo par de tracks con la reducción aplicada a cada uno por separado.

### 1.5 — Multiplicador de madurez (Libro I, Tabla 4)

Además de la eficacia "de catálogo" (§1.1), Magerit permite corregirla según cómo de bien gestionada está esa salvaguarda **en la práctica**, no en la teoría:

> *"Para medir los aspectos organizativos, se puede emplear una escala de madurez que recoja en forma de **factor corrector** la confianza que merece el proceso de gestión de la salvaguarda."* — Libro I

**Lo que Magerit fija (oficial, Tabla 4):** solo los dos extremos.

```
L0 inexistente                    = 0%
L1 inicial/ad hoc                 = ?
L2 reproducible pero intuitivo    = ?
L3 proceso definido               = ?
L4 gestionado y medible           = ?
L5 optimizado                     = 100%
```

**Lo que decide el proyecto** (Magerit no fija los intermedios, confirmado con Cris el 08/09/2026): progresión lineal.

| Nivel | Factor |
|---|---|
| L0 | 0% |
| L1 | 20% |
| L2 | 40% |
| L3 | 60% |
| L4 | 80% |
| L5 | 100% |

**Fórmula de aplicación:**

```
eficacia_efectiva = eficacia_nominal_catálogo × factor_madurez
```

**Dónde vive el dato:** en `activo_salvaguardas` (la asignación concreta activo↔salvaguarda), no en el catálogo `salvaguardas` — porque la misma salvaguarda puede estar bien gestionada en un activo y mal en otro, incluso dentro del mismo cliente. Por defecto se asigna `L5` (100%) a toda asignación nueva, para que el comportamiento no cambie respecto a antes de tener este campo, salvo que se ajuste explícitamente.

El factor se aplica **antes** de combinar varias salvaguardas por Bayes (§3) — es decir, primero se corrige cada salvaguarda por su propia madurez, y solo después se combinan entre sí.

---

## 2. Dónde se buscan las salvaguardas — el punto que Magerit no explicita y tuvimos que decidir

**Regla del proyecto (confirmada el 08/09/2026):** la salvaguarda se busca siempre en el activo donde la amenaza **vive de verdad** (el origen), nunca en quien la hereda por dependencia.

### Por qué

Si el activo A depende de B, y B tiene un EDR instalado, el riesgo que A hereda de B ya debe llegar descontado desde el origen (B) — buscar salvaguardas en A (que no tiene nada instalado) daría un resultado incorrecto.

### Cómo se traduce en código

Cada fila de propagación (`agregar()` en `calculo.py`) recibe un parámetro `origen_salvaguarda`:

| Origen de la fila | `origen_salvaguarda` |
|---|---|
| Directa | El propio activo |
| Dependencia | El activo del que se hereda (`cod_d`), no quien hereda |
| Persona asociada (directa) | El propio activo |
| Persona asociada (vía dependencia) | El activo intermedio del que cuelga la persona |

**Efecto verificado:** una salvaguarda asignada a un activo protege automáticamente, sin configuración adicional, a todos los activos que dependen de él — la reducción se propaga por la misma cadena que ya propaga el riesgo.

---

## 3. Combinación de varias salvaguardas sobre la misma amenaza

Magerit define esto mediante un sistema de "escalones" pensado para su herramienta PILAR (máximo entre alternativas, mínimo entre concurrentes) — no encaja directamente con un modelo continuo (0-100%).

**DECISIÓN DEL PROYECTO** (no es Magerit textual): combinar varias salvaguardas independientes con la **suma de Bayes**, la misma fórmula que ya usa este proyecto para combinar caminos de dependencia "diamante" (`Metodologia_App_Riesgos.md` §2.1):

```
combinado = a + b − (a × b)
```

Aplicado por separado a `ei` y a `ep` de todas las salvaguardas que compartan activo + categoría de amenaza — **ya con el factor de madurez de §1.5 aplicado a cada una individualmente**:

```python
def combinar_eficacias(lista_eficacias):
    ei_c, ep_c = 0.0, 0.0
    for ei, ep in lista_eficacias:   # ei, ep ya corregidas por madurez
        a = (ei or 0) / 100.0
        ei_c = ei_c + a - ei_c * a
        b = (ep or 0) / 100.0
        ep_c = ep_c + b - ep_c * b
    return round(ei_c * 100, 2), round(ep_c * 100, 2)
```

**Ejemplo verificado:** dos salvaguardas con `ei=70%` y `ei=50%` sobre la misma amenaza → combinado = `70 + 50 − 35 = 85%`, no `100%` (no se puede proteger más del 100%) ni `70%` (ignorar la segunda).

---

## 4. Nivel de aplicación de una salvaguarda

**DECISIÓN DEL PROYECTO:** activo + categoría de amenaza (no solo categoría, no por amenaza individual).

### Por qué no "solo por categoría"

Si una salvaguarda protegiera una categoría en todos los activos por igual, una amenaza teórica (ej. un EDR "de catálogo") reduciría el riesgo aunque ese activo concreto no lo tenga instalado de verdad — sobreestimaría la protección real.

### Modelo de datos

```
salvaguardas                    -- catálogo: nombre, tipo, ei, ep, descripción
salvaguarda_categoria           -- qué categorías de amenaza puede proteger cada salvaguarda
activo_salvaguardas             -- asignación real, activo por activo, con su propia madurez (§1.5)
```

Cuando una amenaza de categoría X llega a un activo, el motor busca: *¿el activo origen de esta amenaza tiene alguna salvaguarda asignada cuya categoría coincida con X?* Si hay varias, se combinan (§3), cada una ya corregida por su madurez (§1.5); si no hay ninguna, el Riesgo Actual = Riesgo Potencial.

---

## 5. Catálogo de salvaguardas — ENS Anexo II (RD 311/2022)

Se cargó como catálogo de referencia el Anexo II del Esquema Nacional de Seguridad — **74 medidas** (texto consolidado BOE, actualización 06/11/2024).

### Qué es oficial y qué es criterio del proyecto

| Dato | Origen |
|---|---|
| Código, nombre de cada medida | 100% oficial (BOE-A-2022-7191) |
| Tipo Magerit (PR/DR/EL/IM/CR/RC/MN/DC/AW) | **Criterio del proyecto** — ni el BOE ni Magerit clasifican así las medidas ENS |
| Eficacia (`ei`/`ep`) | **Criterio del proyecto**, tabla fija por tipo — Magerit es explícito en que la eficacia *"se estimará [...] en cada caso concreto"* (Libro I), no existe una tabla oficial |
| Categoría de amenaza protegida | **Criterio del proyecto**, asignada por función de cada medida |

### Fuente usada para calibrar la eficacia por tipo

En vez de asignar 74 números a ojo, se usó una tabla fija de 9 valores (uno por tipo), **calibrada** — no copiada literalmente — con los porcentajes reales del **CIS Community Defense Model v2.0** (Center for Internet Security, [cisecurity.org/insights/white-papers/cis-community-defense-model-2-0](https://www.cisecurity.org/insights/white-papers/cis-community-defense-model-2-0)), que cuantifica con datos de MITRE ATT&CK y estadísticas de amenazas reales cuánto protege cada tipo de control frente a los 5 ataques más comunes (Ransomware 78%/92% IG1/todos los controles, Malware 77%/94%, Ataques web 86%/98%, según la Figura 1 del informe). Es un catálogo distinto al ENS (CIS Controls v8, no Anexo II), así que el cruce sigue siendo criterio del proyecto — pero informado por datos reales, no inventado desde cero.

| Tipo | `ei` | `ep` |
|---|---|---|
| PR Preventiva | 0% | 60% |
| DR Disuasoria | 0% | 30% |
| EL Eliminatoria | 0% | 80% |
| IM Minimizadora | 60% | 0% |
| CR Correctiva | 50% | 0% |
| RC Recuperativa | 40% | 0% |
| MN Monitorización | 20% | 20% |
| DC Detección | 40% | 0% |
| AW Concienciación | 0% | 40% |

*(los 3 últimos — MN/DC/AW — llevan eficacia directa provisional; conceptualmente Magerit los define como "consolidan el efecto de las demás", no como reductores directos. Se dejan así, sobre los que además se puede aplicar el multiplicador de madurez de §1.5 igual que a cualquier otra salvaguarda.)*

### Resultado de la carga

| | Cantidad |
|---|---|
| Salvaguardas cargadas | 74 |
| Vínculos a categorías de amenaza | 87 |
| Medidas descartadas | `op.acc.7`/`op.acc.8` — no existen en el texto oficial del BOE (confirmado) |

---

## 6. Verificación

### 6.1 — Con datos de prueba (aislados, sin ambigüedad)

> **Corrección (09/09/2026, 2ª vez):** el `Riesgo Actual` de este ejemplo (y del ejemplo de propagación de abajo) volvió a estar mal en la versión anterior del documento — esta vez porque la fórmula de `Impacto_residual` no era la de Magerit (reducía la degradación en vez del impacto, ver §1.3). El responsable de área detectó la discrepancia comparando un caso de prueba a mano (`Riesgo Actual` debía dar 1,70, no 3,98). Los valores de abajo están re-verificados ejecutando `calculo.py` con la fórmula ya corregida.

Activo con Impacto=7, Probabilidad="Alta" (ARO=10), con dos salvaguardas asignadas (`ei=70%/ep=40%` y `ei=50%/ep=0%`, ambas a madurez L5):

```
Riesgo Potencial: 6,54
Riesgo Actual:    1,70   (eficacia combinada: ei=85%, ep=40%; Impacto residual = 7×(1-0,85) = 1,05)
```

Y verificación de propagación: activo INF-01 (sin salvaguardas propias) heredando de SRV-01 (con salvaguardas, misma amenaza):

```
INF-01 Dependencia — Potencial: Impacto=5,00 / Riesgo=5,00  →  Actual: Impacto=0,75 / Riesgo=1,47   (protegido sin tener nada asignado)
```

Cálculo aislado del factor de madurez: salvaguarda con `ei=70%/ep=40%` nominal, asignada con madurez `L2` (40%) → eficacia efectiva `ei=28%/ep=16%`. Con madurez `L5` (100%), la eficacia efectiva coincide exactamente con la nominal (no rompe el comportamiento anterior a tener este campo).

### 6.2 — Con datos reales del proyecto

Activo "011 · SO de servidores", amenaza de categoría "Explotación de vulnerabilidades / Malware", con la salvaguarda `[op.exp.6] Protección frente a código dañino` asignada (Acumulado, madurez L5):

```
Riesgo Potencial: 6,87
Riesgo Actual (fórmula corregida): 5,40
```

Confirma la corrección: con la fórmula anterior (incorrecta) el Actual daba 6,10 — una reducción de solo 0,77 respecto al Potencial. Con la fórmula corregida (reduce el Impacto directamente, no la degradación), la reducción es mayor: 1,47 — coherente con lo esperado, ya que la fórmula anterior diluía el efecto de la salvaguarda al volver a pasar por la tabla de degradación.

---

## 7. Verificación completa del catálogo ENS (08/09/2026)

Las 13 medidas que habían quedado sin confirmar línea a línea (`mp.sw`, `mp.info`, `mp.s`) se verificaron contra el BOE y varias guías CCN-STIC (825, 886, 887, 891, 852). Se encontraron y corrigieron **3 errores reales**, ya aplicados en Supabase:

| Medida | Error original | Corrección |
|---|---|---|
| `mp.sw.1` | Se asumió aplicable en Básica | No aplica en Básica — solo Media/Alta |
| `mp.info.7` | Código incorrecto para "Copias de seguridad" | El código real es `mp.info.9` (la familia salta de `.6` a `.9`) |
| `mp.s.3` / `mp.s.4` | Nombres y códigos intercambiados; "Otros servicios" no existe en el ENS | `mp.s.3` = Protección de la navegación web (real, EL, Explotación de vulnerabilidades/Malware). `mp.s.4` = Protección frente a denegación de servicio |

No queda ningún pendiente abierto sobre el catálogo ENS.

---

## 8. Resumen de trazabilidad

| Elemento | Fuente |
|---|---|
| Dos eficacias (`ei`/`ep`) | Magerit Libro I §3.1.7 (cita literal) |
| Fórmulas de degradación/probabilidad residual | Magerit Libro III, "Análisis algorítmico" (cita literal) |
| Reutilización de `impacto_tabla()` y la fórmula log-lineal del Potencial | Decisión del proyecto — mismo motor, valores reducidos |
| Búsqueda de salvaguardas en el activo origen (no en quien hereda) | Decisión del proyecto, 08/09/2026 |
| Combinación por suma de Bayes | Decisión del proyecto (Magerit usa "escalones", no aplicable a un modelo continuo) |
| Nivel de aplicación activo+categoría | Decisión del proyecto |
| Multiplicador de madurez, los dos extremos (L0=0%/L5=100%) | Magerit Libro I, Tabla 4 (cita literal) |
| Multiplicador de madurez, valores intermedios (L1-L4) | Decisión del proyecto — Magerit no los fija |
| Catálogo ENS Anexo II | Oficial (BOE-A-2022-7191) para nombres/códigos; criterio del proyecto para tipo/eficacia/categoría, calibrado con CIS CDM v2.0 |
