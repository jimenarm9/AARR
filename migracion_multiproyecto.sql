-- migracion_multiproyecto.sql
-- Añade soporte multiproyecto a la base de datos existente:
--   - tabla `proyectos`
--   - columna `activos.proyecto_id` (cada activo pertenece a un proyecto)
--   - unicidad de `activos.codigo` pasa de ser global a ser por proyecto
--
-- Es una migración ADITIVA (no borra nada): los activos ya existentes se
-- asignan automáticamente a un proyecto por defecto ('Proyecto 1') que
-- esta migración crea si hace falta. dependencias / personas_asociadas /
-- activo_salvaguardas no necesitan cambios: cuelgan de activos.id, que ya
-- queda ligado a su proyecto a través de activos.proyecto_id.
--
-- Ejecutar una sola vez, en el SQL Editor de Supabase.

-- 1. Tabla de proyectos.
CREATE TABLE IF NOT EXISTS proyectos (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  nombre       TEXT NOT NULL,
  sector       TEXT,
  tamano       TEXT,
  descripcion  TEXT,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),

  CONSTRAINT proyectos_nombre_key UNIQUE (nombre)
);

-- 2. Proyecto por defecto, solo si la tabla está vacía (primera vez que se
--    ejecuta esta migración) -- para poder asignarle los activos ya existentes.
INSERT INTO proyectos (nombre)
SELECT 'Proyecto 1'
WHERE NOT EXISTS (SELECT 1 FROM proyectos);

-- 3. Columna proyecto_id en activos, de momento sin NOT NULL (para poder
--    rellenarla antes de exigirla).
ALTER TABLE activos ADD COLUMN IF NOT EXISTS proyecto_id UUID REFERENCES proyectos(id) ON DELETE CASCADE;

-- 4. Asignar los activos existentes (si los hay) al proyecto por defecto.
UPDATE activos SET proyecto_id = (SELECT id FROM proyectos ORDER BY created_at LIMIT 1)
WHERE proyecto_id IS NULL;

-- 5. A partir de aquí, todo activo tiene que pertenecer a un proyecto.
ALTER TABLE activos ALTER COLUMN proyecto_id SET NOT NULL;

-- 6. El código de activo era único globalmente; pasa a ser único DENTRO de
--    cada proyecto (dos proyectos distintos sí pueden reutilizar 'INF-01').
--    El nombre por defecto que da Postgres a "codigo TEXT UNIQUE NOT NULL"
--    es activos_codigo_key -- si en tu base tiene otro nombre, edita esta
--    línea con el nombre real (Table Editor -> activos -> Constraints).
ALTER TABLE activos DROP CONSTRAINT IF EXISTS activos_codigo_key;
ALTER TABLE activos ADD CONSTRAINT activos_proyecto_codigo_key UNIQUE (proyecto_id, codigo);

CREATE INDEX IF NOT EXISTS idx_activos_proyecto ON activos(proyecto_id);
