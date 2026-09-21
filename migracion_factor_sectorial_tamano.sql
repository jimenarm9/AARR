-- =====================================================================
-- migracion_factor_sectorial_tamano.sql
-- Crea las tablas factor_sectorial y factor_tamano_empresa que usa
-- cargar_catalogo() en app.py (pestaña Amenazas) para el Factor de
-- Riesgo Contextual. Nunca llegaron a un script de migracion en el
-- repo -- de ahi el error "Could not find the table 'public.factor_sectorial'
-- in the schema cache" al faltar en la base de datos.
--
-- Una fila por categoria de amenaza (categorias_amenaza.id):
--   factor_sectorial: FS del sector fijo (Banca / Infraestructuras
--     financieras -- unico investigado por ahora, de ahi que no haya
--     columna "sector").
--   factor_tamano_empresa: FE por tamano de empresa, tres filas por
--     categoria (Pequeña / Mediana / Gran empresa).
--
-- Se siembran ambas con 1.0 (factor neutro, sin ajuste) para que la
-- app funcione de inmediato con "Aplicar Factor de Riesgo Contextual"
-- activado; sustituir esos valores por los reales del estudio
-- sectorial/de tamaño cuando esten disponibles.
-- =====================================================================

CREATE TABLE IF NOT EXISTS factor_sectorial (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  categoria_id  INT NOT NULL REFERENCES categorias_amenaza(id) ON DELETE CASCADE,
  fs            NUMERIC(4,2) NOT NULL DEFAULT 1.0 CHECK (fs > 0),
  UNIQUE (categoria_id)
);

CREATE TABLE IF NOT EXISTS factor_tamano_empresa (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  categoria_id    INT NOT NULL REFERENCES categorias_amenaza(id) ON DELETE CASCADE,
  tamano_empresa  TEXT NOT NULL CHECK (tamano_empresa IN ('Pequeña empresa', 'Mediana empresa', 'Gran empresa')),
  fe              NUMERIC(4,2) NOT NULL DEFAULT 1.0 CHECK (fe > 0),
  UNIQUE (categoria_id, tamano_empresa)
);

-- Siembra: una fila FS=1.0 por categoria existente (no pisa filas que
-- ya pudieran existir, por si el error era solo de cache de esquema).
INSERT INTO factor_sectorial (categoria_id, fs)
SELECT c.id, 1.0
FROM categorias_amenaza c
WHERE NOT EXISTS (
  SELECT 1 FROM factor_sectorial fs WHERE fs.categoria_id = c.id
);

-- Siembra: FE=1.0 por categoria x tamano de empresa.
INSERT INTO factor_tamano_empresa (categoria_id, tamano_empresa, fe)
SELECT c.id, t.tamano_empresa, 1.0
FROM categorias_amenaza c
CROSS JOIN (VALUES ('Pequeña empresa'), ('Mediana empresa'), ('Gran empresa')) AS t(tamano_empresa)
WHERE NOT EXISTS (
  SELECT 1 FROM factor_tamano_empresa fe
  WHERE fe.categoria_id = c.id AND fe.tamano_empresa = t.tamano_empresa
);

-- Tras ejecutar este script en Supabase (SQL editor), si PostgREST
-- sigue sin ver las tablas, recargar el schema cache:
--   NOTIFY pgrst, 'reload schema';
-- (o Settings -> API -> "Reload schema" en el dashboard).
