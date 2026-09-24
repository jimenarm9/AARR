# Metodología de la aplicación de riesgos (Modelo de Riesgo TRC)

Documento de contexto que recoge la metodología aplicada en la aplicación de riesgos (`app.py` + `calculo.py`, datos en Supabase). Basada en Magerit 3.0 (Libro I §8.3 y tarea MAR.12; Libro II, fichas de activos, Sección 2; Libro III, análisis algorítmico), con las decisiones propias de TRC documentadas aparte.

---

## 1. Modelo de dependencias entre activos

### 1.1 Jerarquía

```
Información  (siempre Activo Superior — el valor reside en ella)
     │  depende de
     ▼
Servicio  (la gestiona; a su vez Superior respecto a lo de abajo)
     │  depende de
     ▼
Software · Equipamiento · Comunicaciones  (Activos Inferiores)

Servicio, Aplicación, Equipo... ── también dependen de ──> Personas relacionadas
                                                             (usuarios, operadores,
                                                              administradores, desarrolladores...)
```

- La **Información nunca es Activo Inferior** de un Servicio: el valor está en la información, y es el Servicio quien puede comprometerla si falla.
- **No se encadenan dependencias.** Regla de Magerit: evitar "servicio → aplicación → equipo"; en su lugar, "servicio → aplicación" y "servicio → equipo" como relaciones independientes, cada una colgando directamente del servicio.
- **Las personas relacionadas son un Activo Inferior más, no una categoría aparte.** Según la ficha oficial de cada tipo de activo (Libro II, Sección 2), "las dependencias normalmente identifican... personas relacionadas: usuarios/operadores/administradores/desarrolladores...". En esta aplicación cada persona es un **activo de tipo Personal** del proyecto, asociado a un **Servicio** siempre al **100%**, y se trata exactamente como una dependencia más (§2.2). No hay una fórmula distinta para el personal.
- La única particularidad real está en la ficha propia de **[P] Personal**: mirando hacia abajo (qué depende el personal de algo inferior), Magerit dice literalmente "no suelen identificarse dependencias" — el personal es un nodo terminal del árbol, no depende de nada más abajo.

### 1.2 Estructura de datos (base de datos de la aplicación)

Todo el modelo vive en Supabase (PostgreSQL). Las tablas principales:

| Tabla | Contenido |
|---|---|
| `proyectos` | Un proyecto por cliente o análisis. Activos, dependencias y personas pertenecen a un proyecto |
| `activos` | Código, Nombre, Tipo Magerit, valoración propia D/I/C/A/T (escala 0-10, §1.4) |
| `activo_subtipos` | Subtipos de cada activo (varios por activo) |
| `subtipos_catalogo` | Subtipos permitidos por tipo Magerit y su Subcategoría de amenaza (Regla B, §4). Común a todos los proyectos |
| `dependencias` | Activo Superior, Activo Inferior, Grado (5 niveles, §1.3), Justificación |
| `personas_asociadas` | Servicio, activo Personal, Tipo de rol, Justificación. Grado siempre 100% |
| `catalogo_amenazas` + `amenaza_tipo_activo` + `categorias_amenaza` | Catálogo de amenazas, **común a todos los proyectos** |
| `ajustes_amenaza_activo` | Cambios de una amenaza en un activo concreto: eliminarla, o sobrescribir su probabilidad o degradación (§4.2) |

El **Tipo de rol** de una persona asociada (Usuario, Operador, Administrador, Desarrollador, Responsable) se guarda como dato, pero **no interviene en el cálculo** por ahora. Queda reservado para, en el futuro, modular el impacto de las amenazas de tipo Personal según el rol.

### 1.3 Grado de dependencia: de dónde sale

Magerit **no da una fórmula** para este valor, ni siquiera para el de las personas — lo dice explícitamente: es una estimación experta, obtenida por entrevistas con los responsables del activo superior o por valoración Delphi cuando hay varios expertos. No existe en ninguna fuente pública un valor "correcto" preestablecido; ni Magerit, CCN-STIC, ni ENISA/DBIR lo publican, porque depende de la arquitectura y organización concretas.

Como complemento metodológico (no como sustituto de Magerit):
- **Capa de negocio** (Información/Servicio/Personas): Business Impact Analysis, ISO/TS 22317:2021 — entrevistas estructuradas con responsables de proceso. Para personas, la pregunta guía es: *si esta persona/rol falla, es sustituida o se ve comprometida, ¿en qué grado se ve perjudicado el activo que depende de ella?*
- **Capa técnica** (Servicio→Software/Equipamiento/Comunicaciones): dato objetivable desde CMDB/inventario o arquitectura real (p. ej. redundancia activa-pasiva vs. punto único de fallo), no una opinión.

**Cinco niveles fijos (decisión de TRC).** Para no aparentar una precisión que no existe, el grado no se introduce como un porcentaje libre, sino eligiendo uno de estos cinco niveles, igual que la Valoración y la Probabilidad se eligen por banda:

| Grado | Nivel |
|---|---|
| 100% | Dependencia total — impacto directo |
| 75% | Dependencia total — impacto atenuado |
| 50% | Dependencia relevante, no crítica |
| 25% | Dependencia parcial |
| 10% | Dependencia residual |

Esta escala **no procede de Magerit**: en el modelo cualitativo (Libro III §2.2.1) la dependencia es booleana (existe o no), y en el cuantitativo (§2.2.2) es una fracción continua. Los cinco niveles son un punto intermedio propio. Las dependencias creadas antes de este cambio con otro porcentaje se muestran, al editarlas, en el nivel más cercano (cortes en 87,5 / 62,5 / 37,5 / 17,5%).

Las **personas asociadas** no usan esta escala: su grado es siempre 100% (§2.2).

### 1.4 Criterio de valoración de activos (escala 0-10)

Decidido el 01/09/2026: la valoración propia de cada activo se cumplimenta usando la escala oficial de Magerit 3.0, Libro II – Catálogo de Elementos, capítulo 4 "Criterios de valoración" (páginas 19-24 de 75).

Es una única escala común de 0 a 10, deliberadamente logarítmica, aplicada igual a las cinco dimensiones (D, I, C, A, T) — no hay una escala numérica distinta por dimensión. Un mismo activo puede recibir valores distintos en cada dimensión (p. ej. 10 en Confidencialidad y 3 en Disponibilidad), pero siempre dentro de esta misma tabla:

| Valor | Criterio | Descripción |
|---|---|---|
| 10 | Extremo | Daño extremadamente grave |
| 9 | Muy alto | Daño muy grave |
| 6 – 8 | Alto | Daño grave |
| 3 – 5 | Medio | Daño importante |
| 1 – 2 | Bajo | Daño menor |
| 0 | Despreciable | Irrelevante a efectos prácticos |

Para situar el activo en la escala, cada dimensión responde a su propia pregunta guía (Libro II, capítulo 3):

- **Disponibilidad**: ¿qué importancia tendría que el activo no estuviera disponible?
- **Integridad**: ¿qué importancia tendría que los datos fueran modificados fuera de control?
- **Confidencialidad**: ¿qué importancia tendría que el dato fuera conocido por personas no autorizadas?
- **Autenticidad**: ¿qué importancia tendría que quien accede al servicio (o a los datos) no sea realmente quien se cree?
- **Trazabilidad**: ¿qué importancia tendría que no quedara constancia fehaciente del uso del servicio o del acceso a los datos?

Como apoyo para justificar el nivel en contextos concretos (no son escalas alternativas, son criterios auxiliares dentro de esta misma escala 0-10), Magerit ofrece tablas específicas para: información de carácter personal [pi], obligaciones legales [lpo], seguridad [si], intereses comerciales o económicos [cei], interrupción del servicio [da], orden público [po], operaciones [olm], administración y gestión [adm], pérdida de confianza/reputación [lg], persecución de delitos [crm], tiempo de recuperación del servicio [rto], e información clasificada nacional/UE ([lbl.nat]/[lbl.ue]).

En la aplicación, la valoración se elige siempre por el **nombre de la banda** (Despreciable, Bajo, Medio, Alto, Muy Alto, Extremo), sin mostrar el número. Internamente se guardan los valores 0 / 1,5 / 4 / 7 / 9 / 10 (punto medio de cada banda), que son los que usa el cálculo. "n.a" indica que la dimensión no aplica.

---

## 2. Cómo se transmite la degradación

### 2.1 Grado transitivo (dependencias activo→activo)

Cuando la cadena tiene más de un tramo, el grado que llega al activo de origen se calcula **multiplicando** el grado de cada tramo intermedio:

```
grado_transitivo(A → C, vía B) = grado(A → B) × grado(B → C)
```

Ejemplo real usado en las pruebas: Información Servicio Correos depende de Servicio Correo Clientes (100%), que depende de Red LAN (50%) → grado transitivo Información→Red LAN = 100% × 50% = **50%**.

**Si existe más de un camino hasta el mismo activo inferior (estructuras de diamante)** — p. ej. un activo que depende de dos servicios distintos, y ambos dependen a su vez de la misma Red LAN — Magerit exige combinar los caminos, no quedarse con el más alto (Libro III §2.2.2, "Las dependencias entre activos"):

```
grado(A⇒C) = Σᵢ { grado(A⇒Bi) × grado(Bi→C) }
```

donde la suma **no es aritmética** — se calcula como `a + b = 1 − (1−a) × (1−b)` (la "suma de Bayes", tomada del cálculo de probabilidades), precisamente para que la dependencia combinada nunca pueda superar el 100% por muchos caminos que existan. 

*Ejemplo:* un activo A depende de B1 al 80% y de B2 al 60%, y ambos dependen por completo (100%) del mismo activo C → grado(A⇒C) = (0,80×1,00) combinado con (0,60×1,00) = 0,80 + 0,60 − (0,80×0,60) = **0,92** (92%) — más alto que cualquiera de los dos caminos por separado, nunca el máximo de ambos.

### 2.2 Personas asociadas: una dependencia más al 100%

Una persona asociada es un vínculo **Servicio → activo Personal** con grado fijo del 100%. El cálculo la trata exactamente como cualquier otra dependencia:

- **Valor acumulado**: el activo Personal hereda el valor del Servicio (y de lo que depende de él), por la regla del máximo de §2.5.
- **Amenazas heredadas**: el Servicio, y todo lo que depende del Servicio, hereda las amenazas que la Regla A/B (§4) asigna al propio activo Personal, con los ajustes que se le hayan hecho (§4.2). La degradación llega con el grado transitivo hasta el Servicio × 100%.

En las tablas de resultados, las amenazas que llegan por esta vía se etiquetan con el origen **"Persona asociada"** en lugar de "Dependencia", solo para distinguirlas; el cálculo es idéntico.

### 2.3 Degradación repercutida

La degradación que trae cada amenaza en el catálogo **no cambia** — es un dato fijo por amenaza y tipo de activo. Lo que hace el grado transitivo (§2.1, personas incluidas según §2.2) es atenuar cuánto de esa degradación llega al activo superior (fórmula de *impacto repercutido*, Magerit Libro III):

```
Degradación repercutida = Degradación (catálogo, o ajustada en ese activo según §4.2) × Grado transitivo
```

Esta atenuación (degradación repercutida) es el factor que, multiplicado por el valor propio del activo destino, da el Impacto repercutido completo — ver §2.5.

### 2.4 Tres orígenes de amenaza por activo

Para cada activo, las amenazas que se le atribuyen vienen de tres fuentes:

| Origen | De dónde sale | Grado aplicado |
|---|---|---|
| **Directa** | Amenazas asignadas al propio activo por la Regla A/B (§4), con sus ajustes (§4.2) | 100% |
| **Dependencia** | Amenazas de cada activo del que depende, transitivamente por toda la cadena | Grado transitivo (§2.1) |
| **Persona asociada** | Amenazas de los activos Personal asociados a un Servicio de la cadena | Grado transitivo (§2.1, con el tramo de la persona al 100%) |

### 2.5 Valor acumulado cualitativo y cálculo del Impacto

**Valor acumulado** (Magerit Libro III, modelo *cualitativo*): cada activo hereda el valor de lo más importante que depende de él, sin sumarlo — se usa el **máximo**, no una suma, porque la escala 0-10 es logarítmica/ordinal (§1.4) y sumar puntos de esa escala no tiene una interpretación coherente:

```
valor_acumulado(B) = max( valor_propio(B), max{ valor_acumulado(Ai) } )
```

para cada superior directo Ai de B, por cada dimensión por separado. Se calcula recorriendo el grafo de dependencias en orden topológico (de arriba hacia abajo), y el resultado siempre se queda dentro de la escala 0-10 de los valores propios de entrada — nunca la desborda, a diferencia de la suma que se usó en una versión anterior de este modelo. El grado de dependencia **no atenúa** este máximo (a diferencia de la degradación repercutida, §2.3): un superior al 10% aporta su valor completo, igual que uno al 100% — así lo define Magerit para este modelo.

*Consecuencia a tener presente:* con los cinco niveles de grado (§1.3), una dependencia "residual" (10%) transmite el valor completo del activo superior, igual que una "total". Es fiel a Magerit (en el modelo cualitativo la dependencia es booleana: si existe, transmite valor), pero puede resultar poco intuitivo al leer los resultados. Queda anotado como punto a revisar (§5).

*Ejemplo real:* Red LAN (Comunicaciones) no tiene valor propio alto por sí misma, pero de ella dependen varios Servicios con valor 7 en Disponibilidad → valor_acumulado(Red LAN, D) = max(valor_propio, 7, 7, ...) = **7**, no la suma de todos ellos.

**Impacto** (Libro III, "impacto = v × d"): usa una fórmula distinta según el origen de la amenaza, con dos valores de "v" que no deben confundirse:

| Origen | Fórmula | Valor usado |
|---|---|---|
| **Directa** (amenaza propia del activo) | `Impacto = valor_acumulado(activo) × degradación` | Valor **acumulado** — porque si este activo cae, arrastra todo lo que depende de él |
| **Dependencia / Persona asociada** (amenaza heredada) | `Impacto = valor_propio(activo_destino) × degradación_repercutida` | Valor **propio** — nunca el acumulado, para no contar dos veces lo que ya cuelga de él |

`degradación_repercutida` es la definida en §2.3 (degradación del catálogo o ajustada × grado transitivo).

*Ejemplo real:* Servicio Correo Clientes, valor acumulado D=4, amenaza "Denegación de servicio" con degradación D=90% → Impacto acumulado = 4 × 0,90 = **3,6**. El caso repercutido (amenaza heredada de un activo del que se depende) tiene su propio ejemplo completo en §6.3.2.

#### 2.5.1 Tabla de Impacto  — método Cualitativo (§6.3)

La fórmula `Impacto = valor × degradación` de arriba es la que sigue usando el método **Cuantitativo** (§6.4). El método **Cualitativo** (§6.3), en cambio, usa una **tabla** más generosa que la multiplicación directa a partir de una degradación del 50% en adelante — protege el Impacto de que caiga proporcionalmente igual de rápido que la degradación.

| Degradación | Etiqueta | Valor=10 | 9 | 8 | 7 | 6 | 5 | 4 | 3 | 2 | 1 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 100% | Total o casi total | 10 | 9 | 8 | 7 | 6 | 5 | 4 | 3 | 2 | 1 |
| 90% | Total o casi total | 10 | 9 | 8 | 7 | 6 | 5 | 4 | 3 | 2 | 1 |
| 50% | Alta | 9 | 8 | 7 | 6 | 5 | 4 | 3 | 2 | 1 | 0 |
| 25% | Relevante | 8 | 7 | 6 | 5 | 4 | 3 | 2 | 1 | 0 | 0 |
| 10% | Parcial | 7 | 6 | 5 | 4 | 3 | 2 | 1 | 0 | 0 | 0 |
| 1% | Casi irrelevante | 4 | 3 | 2 | 1 | 0 | 0 | 0 | 0 | 0 | 0 |

El Valor de entrada es siempre el que corresponda según el origen de la amenaza (acumulado para Directa, propio para Dependencia/Persona asociada — misma distinción que arriba), redondeado al entero más cercano (1-10) para poder indexar la tabla. La degradación continua del catálogo se redondea a la fila más cercana usando el punto medio entre filas consecutivas como corte: ≤5,5%→1%, ≤17,5%→10%, ≤37,5%→25%, ≤70%→50%, >70%→90%/100% (da igual cuál, son idénticas).

**Estado actual: activa para el método Cualitativo.** `calculo.py` usa esta tabla (`impacto_tabla()`) para `{dim}_impacto_cualitativo` en los dos orígenes (Directa y Dependencia/Persona asociada). El método Cuantitativo (§6.4) sigue usando `valor × degradación` sin cambios — los dos métodos ya no comparten el mismo paso de Impacto.

---

## 3. Filtros aplicados sobre el resultado

### 3.1 Dimensión relevante por tipo de activo

Convención adoptada para este análisis (no es una regla obligatoria de Magerit, es una simplificación del equipo):

- **Servicio**: solo cuenta **Disponibilidad (D)**. El resto de dimensiones se muestran en blanco/gris ("no aplica").
- **Información**: cuentan **Confidencialidad, Integridad, Autenticidad, Trazabilidad (C/I/A/T)**. Disponibilidad se muestra en blanco/gris.
- El resto de tipos de activo (Software, Equipamiento, Comunicaciones, Instalaciones, Personal) no se ven afectados — mantienen las 5 dimensiones.

### 3.2 Exclusión por degradación 0

Una amenaza **no se asigna/no se propaga** a un activo si su valor en la dimensión relevante es exactamente 0:

- Servicio: se excluye si D = 0.
- Información: se excluye si C = I = A = T = 0 simultáneamente.

**Punto crítico de implementación**: este filtro se aplica **según el tipo del activo que recibe la amenaza** (el destino), no según el tipo original de la amenaza en el catálogo. Una amenaza de Comunicaciones con D=0 (p. ej. "Emanaciones electromagnéticas") no tiene ningún problema para asignarse a un activo de Comunicaciones — pero si llega a un Servicio por dependencia, ahí sí debe filtrarse, porque para un Servicio D=0 no aporta nada. El filtro se revisa en cada fila de salida (Directa, Dependencia y Persona asociada), no solo en el momento de la asignación original.

**Caso de nodo intermedio**: si una amenaza de Servicio tiene D=0 pero C/I/A/T con valor, no cuenta para el propio Servicio (se excluye ahí) — pero si una Información depende de ese Servicio, la amenaza **sí debe propagarse** a la Información, porque sus dimensiones relevantes (C/I/A/T) no son cero. Cada activo de la cadena se evalúa de forma independiente contra sus propias dimensiones relevantes; que una amenaza se excluya en un nodo intermedio no debe impedir que llegue a otro activo para el que sí es relevante. El motor busca las amenazas de cada activo **sin aplicar antes el filtro propio de ese activo**, y decide la inclusión únicamente en el momento de atribuir la fila a cada destino final.

Caso real verificado: "Pérdida de información en cloud" y "Error en la gestión de los datos" (subcategoría Cloud, D=0, C=80 y C=50 respectivamente) no se asignan a Correo TRC (Servicio, SUBTIPO CLOUD) — pero Información Servicio Correos depende de Correo TRC, así que ambas **sí aparecen** en su listado de amenazas por dependencia, con su Confidencialidad intacta.

---

## 4. Cómo se asignan las amenazas a los activos

- **Regla A** (genérica): una amenaza **sin subcategoría** se asigna a todo activo cuyo tipo Magerit coincide exactamente con el "Activo principal" de la amenaza.
- **Regla B** (específica): una amenaza **con subcategoría** se asigna solo si se cumplen las dos condiciones: (1) el tipo Magerit del activo coincide con el "Activo principal" de la amenaza (coincidencia estricta; nunca se cruzan tipos), y (2) alguno de los subtipos del activo tiene, en la tabla `subtipos_catalogo`, esa misma subcategoría en la columna `subcategoria_amenaza`. Es una búsqueda exacta en la tabla, no una aproximación por significado.
- **Subtipos**: un activo solo puede tener subtipos que existan en `subtipos_catalogo` para su tipo. La tabla es común a todos los proyectos y se amplía desde la propia aplicación ("Añadir subtipo nuevo al catálogo"), donde también se elige su subcategoría de amenaza entre las que existen en el catálogo. Los subtipos se tratan siempre en **mayúsculas**; la aplicación normaliza al leer y al escribir. Un subtipo sin subcategoría solo recibe las amenazas genéricas de su tipo (Regla A).
- **Amenazas de Inteligencia Artificial**: están cargadas en el catálogo, pero su "Activo principal" (IA) no es uno de los siete tipos Magerit de la aplicación, así que no se asignan a ningún activo. Quedan catalogadas a la espera de que exista un tipo de activo para ellas.

### 4.1 Valores múltiples separados por ";"

En el Excel de amenazas, el "Activo principal" puede traer varios tipos separados por `;` (p. ej. "Servicio;Software"). Al cargarlo en la base de datos se guarda una fila por tipo en `amenaza_tipo_activo`, y la amenaza se evalúa contra cada tipo por separado. Del mismo modo, un activo puede tener varios subtipos (p. ej. CLOUD, WEB y SUBCONTRATADO) y recibe las amenazas de todas las subcategorías correspondientes.

### 4.2 Ajustes de amenazas por activo

El catálogo es común a todos los proyectos, pero cada activo puede apartarse de él sin modificar la base:

- **Eliminar** una amenaza de un activo concreto. Deja de contar para ese activo y **deja de propagarse** a los activos que dependen de él. Se puede volver a añadir desde la página Amenazas ("➕ Añadir amenaza"), que solo ofrece amenazas que la Regla A/B asigna a ese activo y que se han eliminado antes; vuelve con los valores del catálogo base.
- **Ajustar la probabilidad o la degradación** de cualquier dimensión para ese activo. Solo se guardan los valores que difieren del catálogo; el resto sigue el catálogo, de modo que las mejoras posteriores del catálogo llegan a lo que no se ha tocado.

Los ajustes se aplican justo después de la Regla A/B y **antes de propagar**, así que un ajuste en un activo inferior afecta a lo que heredan sus superiores. No se pueden añadir amenazas que la Regla A/B no asigne, ni cambiar subtipos, tipos o subcategorías por proyecto.

### 4.3 Mantenimiento del catálogo

Los ajustes (§4.2) están ligados al identificador de cada amenaza en la base de datos. Por eso el catálogo **no debe recargarse borrando y volviendo a insertar** las amenazas: se generarían identificadores nuevos y se perderían todos los ajustes. Las actualizaciones desde el Excel se hacen con un script que **modifica las amenazas existentes** (identificadas por nombre y subcategoría), **inserta solo las nuevas** y rehace sus tipos de activo. Las amenazas que ya no aparecen en el Excel no se borran: se marcan como no vigentes, para no perder su historia ni sus ajustes.

---

## 5. Estado actual y pendientes reales

El Impacto y el Riesgo se calculan con datos reales de valoración. Lo que sigue pendiente es:

- **Salvaguardas y riesgo residual**: no implementado. El riesgo actual es el potencial, sin descontar la eficacia de los controles existentes. Es el siguiente paso de Magerit.
- **Justificación y registro de cambios**: los ajustes por activo (§4.2) no exigen justificación ni guardan quién ni cuándo los hizo.
- **Criterio de aceptación del riesgo**: las escalas de colores (§7) muestran el nivel, pero no hay un umbral definido a partir del cual el riesgo deba tratarse.
- **Grado y valor acumulado**: con el modelo del máximo, una dependencia residual transmite el valor completo (§2.5). Pendiente de decidir si se mantiene.
- **Rol de las personas**: guardado pero sin efecto en el cálculo (§1.2).
- **Tipo de activo para IA**: las amenazas de IA están catalogadas sin asignar (§4).
- **Factor de Riesgo Contextual**: aparcado (`claude/Propuesta_Factor_Riesgo_Contextual.md`).

---

## 6. Cálculo del Riesgo (Magerit 3.0, Libro III §2.1, §2.2.1 y §2.2.2)

Magerit ofrece, en rigor, **tres** técnicas para pasar de Valor+Degradación a Riesgo (Libro III, "Técnicas específicas"): §2.1 "Análisis mediante tablas", §2.2.1 "Un modelo cualitativo" y §2.2.2 "Un modelo cuantitativo". Lo que este proyecto llama "Cuantitativo" y "Cualitativo" no coincide con una sección aislada cada uno — es una combinación deliberada, explicada aquí con precisión para que quede trazable.

**El criterio real que separa los dos métodos no es "usa ARO / no usa ARO"** — esa fue una simplificación de una versión anterior de este documento, y no es correcta. El criterio real es si se trabaja con una **magnitud económica absoluta** (dinero real — el terreno del §2.2.2) o con una **escala ordinal/relativa** (el terreno del §2.2.1), sea cual sea la forma en que se represente esa escala. La valoración de activos de este proyecto (escala 0-10, Libro II cap. 4) es ordinal desde el origen — nunca ha sido una cifra económica — así que **todo** lo que se construye a partir de ella sigue siendo cualitativo en el sentido de Magerit, incluido un Riesgo final expresado como número continuo 0-10, e incluido el uso del ARO como representación numérica de la Probabilidad (Libro I ya ofrece el ARO como una forma más de expresar esa misma escala cualitativa — no es exclusivo del modelo económico).

| | **Cuantitativo** (§6.4 más abajo) | **Cualitativo** (§6.3 más abajo) |
|---|---|---|
| Naturaleza del Valor | Ordinal (0-10) — igual que el Cualitativo, este proyecto nunca ha tenido valoración económica real | Ordinal (0-10) |
| Valor acumulado | Máximo (§2.2.1) — ver aviso de desviación en §6.4.1 | Máximo (§2.2.1) |
| Degradación | Porcentaje real | Porcentaje real |
| Probabilidad | ARO, multiplicado directamente sin protección | ARO, dentro de una fórmula que da más peso al Impacto (§6.3.3/6.3.4) |
| Paso Impacto | `valor × degradación` | Tabla (§2.5.1) — más generosa que la multiplicación a partir del 50% de degradación |
| Paso Riesgo | `Impacto × ARO` (multiplicación directa) | Fórmula log-lineal ajustada (§6.3.3/6.3.4) que cumple los axiomas de §2.2.1 |
| Salida | Número real, sin acotar (puede superar 1.000) | Número continuo, acotado a 0-10 |

**Por qué el Cuantitativo (§6.4) sigue siendo un método aparte, si los dos usan ARO ahora**: la diferencia ya no es "quién usa ARO", es **cómo lo usa cada uno**. El Cuantitativo lo multiplica sin ninguna protección — si el ARO es minúsculo, el resultado se desploma sea cual sea el Impacto, incumpliendo el axioma de Magerit de "dar más peso al impacto que a la probabilidad" (§2.2.1). El Cualitativo (§6.3.3/6.3.4) usa una fórmula construida específicamente para cumplir ese axioma, evitando que una amenaza catastrófica-pero-rarísima se lea como "riesgo insignificante".

### 6.1 De dónde sale la Probabilidad numérica (ARO)

El catálogo guarda la Probabilidad como valor cualitativo (Muy Alta/Alta/Media/Baja/Muy Baja — Libro I Tabla 1). Para los dos métodos (§6.3 y §6.4) se traduce a la Tabla 2 de Magerit (Libro I §3.1.2) — la misma tasa ARO que ya usa este proyecto en el Factor de Riesgo Contextual (`claude/Propuesta_Factor_Riesgo_Contextual.md` §1):

| Probabilidad | ARO |
|---|---|
| Muy Alta | 100 |
| Alta | 10 |
| Media | 1 |
| Baja | 0,1 |
| Muy Baja | 0,01 |

### 6.3 Método Cualitativo

Basado en Magerit Libro III §2.2.1. Usa el valor acumulado por **máximo** (§2.5) en todos los pasos, la tabla para el Impacto (§2.5.1, en vez de `valor × degradación`) y el ARO como representación numérica de la Probabilidad (§6.1) — pero dentro de una fórmula diseñada para cumplir los axiomas exactos que Magerit exige a cualquier función de riesgo cualitativo:

> "ℜ(0,p)=0; ℜ(v,0)=0; creciente en valor; creciente en probabilidad. Habitualmente se emplea alguna función que dé más peso al impacto que a la probabilidad." (Libro III, §2.2.1)

#### 6.3.1 Impacto acumulado

Se aplica a amenazas de origen **Directa**. Usa el valor **acumulado** del activo (§2.5), porque si este activo cae, arrastra consigo todo lo que se apoya en él:

```
valor_acumulado(activo) = max( valor_propio(activo), max{ valor_acumulado(Ai) } )
```

para cada superior directo Ai del activo, por cada dimensión por separado. El Impacto sale de la **tabla** (§2.5.1), no de multiplicar:

```
Impacto acumulado = tabla_degradacion( valor_acumulado(activo), degradación(amenaza) )
```

*Ejemplo:* activo con valor acumulado 7 (escala 0-10), amenaza **propia de ese activo** que lo degrada un 90% → fila "90% / Total o casi total", columna Valor=7 → Impacto acumulado = **7** (con la multiplicación simple hubiera dado 6,3 — la tabla es más generosa a partir del 50% de degradación).

#### 6.3.2 Impacto repercutido

Se aplica a amenazas de origen **Dependencia** o **Persona asociada** — es decir, amenazas **heredadas**: no atacan directamente al activo superior, atacan a un activo inferior (o a una persona asociada) del que este depende, y su daño repercute hacia arriba. La degradación que se usa es siempre la de esa amenaza heredada, nunca una amenaza propia del superior. Usa el valor **propio** del activo superior — nunca el acumulado, para no contar dos veces lo que cuelga de él:

```
Impacto repercutido = tabla_degradacion( valor_propio(activo_superior), degradación_repercutida(amenaza_heredada) )
```

(`degradación_repercutida` = degradación **de la amenaza que sufre el activo inferior o la persona asociada** × grado transitivo con el que ese daño llega hasta el superior, definida en §2.3.)

*Ejemplo:* activo A con valor propio 4, depende de B al 50%; **B** (no A) sufre una amenaza con degradación 90% → degradación repercutida en A = 90%×50% = 45% → esa degradación cae en la fila "50% / Alta" de la tabla (§2.5.1, cortes 37,5%-70%) → columna Valor=4 → Impacto repercutido en A = **3** (con la multiplicación simple hubiera dado 1,8).

#### 6.3.3 Riesgo acumulado

Parte del Impacto acumulado (§6.3.1):

```
si Impacto acumulado == 0:
    Riesgo acumulado = 0
si no:
    Riesgo acumulado = max( 0,01 ;  0,771 × Impacto acumulado + 1,147 × log₁₀(ARO) )
```

**De dónde salen los coeficientes 0,771 y 1,147**: se calibraron para que el Impacto pese **1,5 veces más** que la Probabilidad en el resultado final, tal como exige el axioma de §2.2.1 ("más peso al impacto que a la probabilidad"). El Impacto recorre 9 puntos en su rango completo (de 1 a 10); `log₁₀(ARO)` recorre 4 puntos en el suyo (de -2, Muy Baja, a +2, Muy Alta). El peso total de cada variable en el recorrido completo es coeficiente × recorrido:

```
peso_Impacto / peso_Probabilidad = (0,771 × 9) / (1,147 × 4) = 6,94 / 4,59 ≈ 1,5
```

Los valores base (antes de fijar el ratio en 1,5) se obtuvieron ajustando una recta a la tabla de riesgo real (ver más abajo, "Origen empírico"), y después se reescalaron ambos por el mismo factor para que el peor caso posible (Impacto=10, Probabilidad Muy Alta) llegue exactamente a 10 — el techo de la escala. Reescalar por el mismo factor no cambia el ratio 1,5, solo estira los dos coeficientes por igual.

**Por qué el suelo de 0,01 y no 0**: la fórmula puede dar negativo en la esquina de Impacto y Probabilidad más bajos a la vez (matemáticamente inevitable si se exige, a la vez, un ratio moderado de 1,5 y que el máximo llegue a 10 — ver "Limitación conocida" más abajo). Un riesgo negativo no tiene ninguna interpretación válida, así que se recorta a 0,01. El caso Impacto=0 se trata aparte, dando exactamente 0 — nunca 0,01 — porque ese sí es un axioma exigido por Magerit (`ℜ(0,p)=0`); el resto de casos nunca se calculan como cero exacto porque, por muy baja que sea la Probabilidad, siempre puede llegar a ocurrir.

**Limitación conocida**: no es matemáticamente posible, con una fórmula de esta forma (suma de un término de Impacto y uno de Probabilidad), tener simultáneamente las tres cosas: (a) el máximo llega a 10, (b) nunca da negativo, y (c) el ratio se queda en un valor moderado como 1,5. Exigir (a) y (b) a la vez fuerza un ratio de al menos 4,5 — mucho más agresivo de lo que se quería. Se ha priorizado el ratio de 1,5 (el requisito de Magerit) y el techo en 10 (para que el peor caso real se lea como el peor de la escala), aceptando el suelo en 0,01 para las pocas celdas donde la suma sale negativa (Impacto 1-3 combinado con Probabilidad Muy Baja o Baja).

**Origen empírico de los coeficientes base**: antes de fijar el ratio en 1,5 y reescalar, se contrastó la forma de esta fórmula contra la tabla de riesgo conocida, ajustando una recta `Riesgo ≈ a×Impacto + b×log₁₀(ARO) + c` a sus 50 valores publicados — con un error medio de solo 0,08 sobre esa tabla. Ese ajuste inicial ya daba un ratio de 1,51 (prácticamente 1,5) de forma natural, sin haberlo impuesto — lo cual refuerza que 1,5 es una elección razonable y no arbitraria.

*Ejemplo:* Impacto acumulado = 7 (del §6.3.1, con la tabla), Probabilidad = Muy Alta (ARO=100) → Riesgo acumulado = max(0,01; 0,771×7 + 1,147×log₁₀(100)) = max(0,01; 5,40 + 2,29) = **7,69**.

#### 6.3.4 Riesgo repercutido

Misma fórmula que 6.3.3, partiendo del Impacto repercutido (§6.3.2) en vez del acumulado:

```
si Impacto repercutido == 0:
    Riesgo repercutido = 0
si no:
    Riesgo repercutido = max( 0,01 ;  0,771 × Impacto repercutido + 1,147 × log₁₀(ARO) )
```

*Ejemplo:* Impacto repercutido = 3 (del §6.3.2, con la tabla ), Probabilidad = Media (ARO=1) → Riesgo repercutido = max(0,01; 0,771×3 + 1,147×log₁₀(1)) = max(0,01; 2,31 + 0) = **2,31**.

### 6.4 Método Cuantitativo

```
Riesgo = Impacto × ARO (Annual Rate of Occurrence, §6.1)
```

Se calcula por activo, por amenaza y por cada dimensión relevante (D/I/C/A/T según el tipo de activo, §3.1), respetando el mismo origen que distingue el modelo: Directa, Dependencia o Persona asociada (§2.4). A diferencia del Cualitativo (§6.3), esta es una multiplicación directa, sin ninguna protección para cuando el ARO es minúsculo — ver la comparativa del principio de esta sección sobre las consecuencias de esto.

#### 6.4.1 Impacto acumulado

Se aplica a amenazas de origen **Directa**.

**Aviso de desviación respecto a Magerit**: el Libro III define, para el modelo cuantitativo (§2.2.2), el valor acumulado como una **suma**, no como un máximo:

```
valor_acumulado(B) = valor(B) + Σ { valor(Ai) × grado(Ai⇒B) }      ← §2.2.2, fórmula oficial del Cuantitativo
```

Esta fórmula está pensada para valoraciones **económicas reales** (dinero) — el propio Magerit lo dice explícitamente para este modelo. En este proyecto, la valoración de activos usa la escala 0-10 **logarítmica/ordinal** de Magerit (Libro II cap. 4, §1.4), no una cifra económica. Sumar puntos de una escala logarítmica no tiene una interpretación coherente, y se comprobó en la práctica: con la suma, activos compartidos por mucha infraestructura (p. ej. Red LAN, de la que dependen varios servicios) alcanzaban valores acumulados de casi 20, muy por encima de la propia escala de origen (0-10).

Por esta razón, **también en el método Cuantitativo se usa aquí el máximo del §2.2.1**, la misma fórmula que en §6.3.1:

```
valor_acumulado(activo) = max( valor_propio(activo), max{ valor_acumulado(Ai) } )
```

```
Impacto acumulado = valor_acumulado(activo) × degradación(amenaza)
```

*Ejemplo:* valor acumulado 7, amenaza **propia de ese activo** con degradación 90% → Impacto acumulado = 7 × 0,90 = **6,3** (se queda como número, no se convierte a nivel).

Si en el futuro se incorpora una valoración económica real de los activos (coste de reposición, facturación afectada, etc.), ahí sí tendría sentido recuperar la suma de §2.2.2 para un método Cuantitativo fiel a Magerit — con la valoración ordinal actual, no.

#### 6.4.2 Impacto repercutido

Se aplica a amenazas de origen **Dependencia** o **Persona asociada** — amenazas **heredadas** de un activo inferior o de una persona asociada, nunca propias del activo superior. Misma fórmula que en §6.3.2:

```
Impacto repercutido = valor_propio(activo_superior) × degradación_repercutida(amenaza_heredada)
```

*Ejemplo:* valor propio 4, **la amenaza heredada del activo del que depende** tiene degradación repercutida 45% (90% en el origen × 50% de grado) → Impacto repercutido = 4 × 0,45 = **1,8**.

#### 6.4.3 Riesgo acumulado

```
Riesgo acumulado = Impacto acumulado (§6.4.1) × ARO(amenaza)
```

*Ejemplo:* Impacto acumulado 6,3, ARO=100 (Muy Alta) → Riesgo acumulado = 6,3 × 100 = **630**.

#### 6.4.4 Riesgo repercutido

```
Riesgo repercutido = Impacto repercutido (§6.4.2) × ARO(amenaza)
```

*Ejemplo:* Impacto repercutido 1,8, ARO=1 (Media) → Riesgo repercutido = 1,8 × 1 = **1,8**.

### 6.5 Relación con el Factor de Riesgo Contextual

Si en el futuro se activa el ajuste FS×FE (aparcado en `claude/Propuesta_Factor_Riesgo_Contextual.md`), tanto el método **Cuantitativo** (§6.4) como la fórmula del **Cualitativo** (§6.3.3/6.3.4) usarían el `ARO_ajustado_contexto` de esa fórmula en vez del ARO base, sin cambiar nada más de esta sección — los dos métodos usan ahora el ARO como entrada, así que el ajuste contextual les afecta a los dos por igual.

### 6.6 Estado actual en la aplicación

Los dos métodos están **calculados** en `calculo.py` para cada amenaza, activo y dimensión (`{dim}_impacto`/`{dim}_riesgo` para el Cuantitativo, `{dim}_impacto_cualitativo`/`{dim}_riesgo_cualitativo` para el Cualitativo — el primero con la tabla de §2.5.1, el segundo con la fórmula log-lineal de §6.3.3/6.3.4; ninguno de los dos usa ya la tabla de 5 niveles de versiones anteriores de este documento). En la aplicación (`app.py`), las páginas **Impacto** y **Riesgo** muestran actualmente **solo la vista Cualitativa** — decisión expresa, para no exponer a la vez un número sin acotar (Cuantitativo) y uno acotado (Cualitativo) antes de que el equipo decida cuál usar como criterio principal. El código de la vista Cuantitativa queda comentado en el propio archivo, no borrado, para reactivarlo sin tener que rehacerlo si se decide lo contrario más adelante.

---

## 7. Presentación de resultados en la aplicación

Decisiones de interfaz propias de TRC (no proceden de Magerit), para que la lectura sea homogénea:

**Valoración, valor acumulado e Impacto** se muestran siempre por el nombre de la banda de la escala de Valor (§1.4), nunca por el número, con estos colores:

| Banda | Color |
|---|---|
| Despreciable | Blanco |
| Bajo | Amarillo claro |
| Medio | Naranja |
| Alto | Rojo claro |
| Muy Alto | Rojo oscuro |
| Extremo | Morado oscuro |

**Riesgo** (método Cualitativo, escala continua 0-10) se muestra con su número y esta escala propia de colores:

| Riesgo | Color |
|---|---|
| < 1 | Verde claro |
| 1 – 2,9 | Amarillo claro |
| 3 – 5,9 | Naranja |
| 6 – 8,5 | Rojo claro |
| 8,6 – 9,5 | Rojo oscuro |
| 9,6 – 10 | Morado oscuro |

Las tablas de Impacto y Riesgo por activo muestran una columna por dimensión (D, I, C, A, T) con el máximo de cada una, y el detalle de amenazas del activo seleccionado. La página **Resumen** muestra, por separado para acumulado y repercutido, cuántos activos hay en cada tramo de riesgo, los diez activos con mayor riesgo e impacto, y las diez combinaciones activo-amenaza más críticas.
