-- =====================================================================
-- Esquema de base de datos — Modelo de riesgo TRC (Magerit 3.0)
-- Para Supabase / PostgreSQL.
--
-- Este fichero es la versión CONSOLIDADA y actual del esquema: parte de
-- schema_supabase.sql (tipos ENUM, comentarios, estructura general) y de
-- base_de_datos.sql (estructura real ya desplegada, con soporte
-- multiproyecto: tabla proyectos + activos.proyecto_id, y el catálogo
-- editable subtipos_catalogo) — ver app.py / calculo.py, que asumen
-- exactamente estas tablas y columnas.
--
-- AVISO: este script empieza con DROP ... CASCADE — borra TODAS las
-- tablas y sus datos antes de recrearlas. Solo ejecutar de forma
-- consciente (recreación completa), nunca sobre una base con datos que
-- se quieran conservar sin haber hecho antes una copia de seguridad.
-- =====================================================================

-- ---------------------------------------------------------------------
-- 0. LIMPIEZA PREVIA
-- ---------------------------------------------------------------------
DROP VIEW IF EXISTS v_catalogo_resumen CASCADE;
DROP VIEW IF EXISTS v_activos_resumen CASCADE;
DROP TABLE IF EXISTS amenaza_tipo_activo CASCADE;
DROP TABLE IF EXISTS catalogo_amenazas CASCADE;
DROP TABLE IF EXISTS categorias_amenaza CASCADE;
DROP TABLE IF EXISTS personas_asociadas CASCADE;
DROP TABLE IF EXISTS dependencias CASCADE;
DROP TABLE IF EXISTS activo_subtipos CASCADE;
DROP TABLE IF EXISTS subtipos_catalogo CASCADE;
DROP TABLE IF EXISTS activos CASCADE;
DROP TABLE IF EXISTS proyectos CASCADE;
DROP TYPE IF EXISTS tipo_rol CASCADE;
DROP TYPE IF EXISTS probabilidad_nivel CASCADE;
DROP TYPE IF EXISTS tipo_magerit CASCADE;

-- Necesario para gen_random_uuid() (normalmente ya está activada en
-- Supabase, pero se deja aquí para que el script sea autocontenido).
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ---------------------------------------------------------------------
-- 1. Tipos controlados (ENUM)
-- ---------------------------------------------------------------------
CREATE TYPE tipo_magerit AS ENUM (
  'INFORMACION', 'SERVICIO', 'SOFTWARE', 'EQUIPAMIENTO',
  'COMUNICACIONES', 'INSTALACIONES', 'PERSONAL'
);

CREATE TYPE probabilidad_nivel AS ENUM (
  'Muy Baja', 'Baja', 'Media', 'Alta', 'Muy Alta'
);

CREATE TYPE tipo_rol AS ENUM (
  'Usuario', 'Operador', 'Administrador', 'Desarrollador', 'Responsable'
);

-- ---------------------------------------------------------------------
-- 2. PROYECTOS — cada proyecto tiene sus propios activos/dependencias/
--    personas; el catálogo de amenazas y subtipos_catalogo son comunes.
-- ---------------------------------------------------------------------
CREATE TABLE proyectos (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  nombre       TEXT NOT NULL,
  sector       TEXT,
  tamano       TEXT,
  descripcion  TEXT,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),

  CONSTRAINT proyectos_nombre_key UNIQUE (nombre)
);

-- ---------------------------------------------------------------------
-- 3. ACTIVOS
-- ---------------------------------------------------------------------
CREATE TABLE activos (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  proyecto_id     UUID NOT NULL REFERENCES proyectos(id) ON DELETE CASCADE,
  codigo          TEXT NOT NULL,               -- 'INF-01', 'SRV-06'... único dentro del proyecto
  nombre          TEXT NOT NULL,
  tipo            tipo_magerit NOT NULL,
  descripcion     TEXT,
  responsable     TEXT,
  ubicacion       TEXT,

  -- Valoración propia, escala 0-10 de Magerit (Libro II, cap. 4).
  -- NULL = "n.a" (dimensión no relevante para este tipo de activo).
  valor_propio_d  NUMERIC(4,2) CHECK (valor_propio_d BETWEEN 0 AND 10),
  valor_propio_i  NUMERIC(4,2) CHECK (valor_propio_i BETWEEN 0 AND 10),
  valor_propio_c  NUMERIC(4,2) CHECK (valor_propio_c BETWEEN 0 AND 10),
  valor_propio_a  NUMERIC(4,2) CHECK (valor_propio_a BETWEEN 0 AND 10),
  valor_propio_t  NUMERIC(4,2) CHECK (valor_propio_t BETWEEN 0 AND 10),

  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

  CONSTRAINT activos_proyecto_codigo_key UNIQUE (proyecto_id, codigo)
);

COMMENT ON COLUMN activos.valor_propio_d IS
  'Disponibilidad. Normalmente solo se rellena en activos tipo SERVICIO.';
COMMENT ON COLUMN activos.valor_propio_c IS
  'Confidencialidad. Normalmente solo se rellena en activos tipo INFORMACION.';

CREATE INDEX idx_activos_proyecto ON activos(proyecto_id);

-- Subtipos del activo — una fila por subtipo, un activo puede tener varios.
CREATE TABLE activo_subtipos (
  activo_id  UUID NOT NULL REFERENCES activos(id) ON DELETE CASCADE,
  subtipo    TEXT NOT NULL,
  PRIMARY KEY (activo_id, subtipo)
);

CREATE INDEX idx_activo_subtipos_subtipo ON activo_subtipos(subtipo);

-- ---------------------------------------------------------------------
-- 4. SUBTIPOS_CATALOGO — catálogo editable de subtipos por tipo Magerit
--    (alimenta el multiselect de Subtipos en la página Activos).
-- ---------------------------------------------------------------------
CREATE TABLE subtipos_catalogo (
  subtipo       TEXT NOT NULL,
  tipo_magerit  tipo_magerit NOT NULL,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (subtipo, tipo_magerit)
);

-- ---------------------------------------------------------------------
-- 5. DEPENDENCIAS (Activo Superior -> Activo Inferior)
-- ---------------------------------------------------------------------
-- Regla de negocio (no forzada aquí por constraint, documentada para
-- quien mantenga los datos): la Información siempre es Superior al
-- Servicio que la gestiona; nunca al revés.
CREATE TABLE dependencias (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  activo_superior_id  UUID NOT NULL REFERENCES activos(id) ON DELETE CASCADE,
  activo_inferior_id  UUID NOT NULL REFERENCES activos(id) ON DELETE CASCADE,
  grado               NUMERIC(5,2) NOT NULL CHECK (grado BETWEEN 0 AND 100),
  justificacion       TEXT,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),

  CONSTRAINT no_auto_dependencia CHECK (activo_superior_id <> activo_inferior_id),
  UNIQUE (activo_superior_id, activo_inferior_id)
);

CREATE INDEX idx_dependencias_superior ON dependencias(activo_superior_id);
CREATE INDEX idx_dependencias_inferior ON dependencias(activo_inferior_id);

-- ---------------------------------------------------------------------
-- 6. PERSONAS_ASOCIADAS
-- ---------------------------------------------------------------------
-- El nombre de la persona/equipo es solo descriptivo: las amenazas se
-- buscan por tipo_rol, no por nombre, así que no hay FK a una tabla de
-- personas.
CREATE TABLE personas_asociadas (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  activo_id       UUID NOT NULL REFERENCES activos(id) ON DELETE CASCADE,
  persona_nombre  TEXT NOT NULL,      -- descriptivo: "Administradores DNS"...
  tipo_rol        tipo_rol NOT NULL,  -- esto sí se usa para buscar amenazas
  grado           NUMERIC(5,2) NOT NULL CHECK (grado BETWEEN 0 AND 100),
  justificacion   TEXT,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_personas_activo ON personas_asociadas(activo_id);

-- ---------------------------------------------------------------------
-- 7. CATÁLOGO DE AMENAZAS (global, compartido por todos los proyectos)
-- ---------------------------------------------------------------------
CREATE TABLE categorias_amenaza (
  id      SERIAL PRIMARY KEY,
  nombre  TEXT UNIQUE NOT NULL     -- 'Ransomware / Extorsión', 'Cloud'...
);

CREATE TABLE catalogo_amenazas (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  amenaza        TEXT NOT NULL,
  categoria_id   INT REFERENCES categorias_amenaza(id),
  probabilidad   probabilidad_nivel NOT NULL,
  subcategoria   TEXT,             -- 'Cloud', 'Maquina Virtual', 'Dominio'... o NULL = genérica

  -- Degradación 0-100%, tal cual el catálogo actual.
  degradacion_d  NUMERIC(5,2) CHECK (degradacion_d BETWEEN 0 AND 100),
  degradacion_i  NUMERIC(5,2) CHECK (degradacion_i BETWEEN 0 AND 100),
  degradacion_c  NUMERIC(5,2) CHECK (degradacion_c BETWEEN 0 AND 100),
  degradacion_a  NUMERIC(5,2) CHECK (degradacion_a BETWEEN 0 AND 100),
  degradacion_t  NUMERIC(5,2) CHECK (degradacion_t BETWEEN 0 AND 100),

  version        TEXT,             -- 'v4', 'v5'... trazabilidad de cuándo se añadió/cambió
  vigente        BOOLEAN NOT NULL DEFAULT true,   -- para retirar sin borrar histórico
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),

  UNIQUE (amenaza, subcategoria)
);

-- Activo principal de la amenaza (multivalor) — una fila por tipo.
CREATE TABLE amenaza_tipo_activo (
  amenaza_id   UUID NOT NULL REFERENCES catalogo_amenazas(id) ON DELETE CASCADE,
  tipo_activo  tipo_magerit NOT NULL,
  PRIMARY KEY (amenaza_id, tipo_activo)
);

CREATE INDEX idx_amenaza_tipo_activo_tipo ON amenaza_tipo_activo(tipo_activo);

-- ---------------------------------------------------------------------
-- 8. Vistas de comprobación rápida
-- ---------------------------------------------------------------------
CREATE VIEW v_activos_resumen AS
SELECT
  a.codigo,
  a.nombre,
  a.tipo,
  string_agg(DISTINCT s.subtipo, ';' ORDER BY s.subtipo) AS subtipos,
  a.valor_propio_d, a.valor_propio_i, a.valor_propio_c, a.valor_propio_a, a.valor_propio_t
FROM activos a
LEFT JOIN activo_subtipos s ON s.activo_id = a.id
GROUP BY a.id;

CREATE VIEW v_catalogo_resumen AS
SELECT
  c.amenaza,
  cat.nombre AS categoria,
  c.probabilidad,
  string_agg(DISTINCT ta.tipo_activo::text, ';' ORDER BY ta.tipo_activo::text) AS activo_principal,
  c.subcategoria,
  c.degradacion_d, c.degradacion_i, c.degradacion_c, c.degradacion_a, c.degradacion_t
FROM catalogo_amenazas c
LEFT JOIN categorias_amenaza cat ON cat.id = c.categoria_id
LEFT JOIN amenaza_tipo_activo ta ON ta.amenaza_id = c.id
WHERE c.vigente
GROUP BY c.id, cat.nombre;

-- ---------------------------------------------------------------------
-- 9. Proyecto por defecto — app.py se detiene con st.error() si la
--    tabla proyectos está vacía, así que dejamos uno creado.
-- ---------------------------------------------------------------------
INSERT INTO proyectos (nombre) VALUES ('Proyecto 1');
