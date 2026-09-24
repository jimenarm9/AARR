-- WARNING: This schema is for context only and is not meant to be run.
-- Table order and constraints may not be valid for execution.

CREATE TABLE public.activos (
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  codigo text NOT NULL,
  nombre text NOT NULL,
  tipo USER-DEFINED NOT NULL,
  descripcion text,
  responsable text,
  ubicacion text,
  valor_propio_d numeric CHECK (valor_propio_d >= 0::numeric AND valor_propio_d <= 10::numeric),
  valor_propio_i numeric CHECK (valor_propio_i >= 0::numeric AND valor_propio_i <= 10::numeric),
  valor_propio_c numeric CHECK (valor_propio_c >= 0::numeric AND valor_propio_c <= 10::numeric),
  valor_propio_a numeric CHECK (valor_propio_a >= 0::numeric AND valor_propio_a <= 10::numeric),
  valor_propio_t numeric CHECK (valor_propio_t >= 0::numeric AND valor_propio_t <= 10::numeric),
  created_at timestamp with time zone NOT NULL DEFAULT now(),
  updated_at timestamp with time zone NOT NULL DEFAULT now(),
  proyecto_id uuid NOT NULL,
  CONSTRAINT activos_pkey PRIMARY KEY (id),
  CONSTRAINT activos_proyecto_id_fkey FOREIGN KEY (proyecto_id) REFERENCES public.proyectos(id)
);
CREATE TABLE public.activo_subtipos (
  activo_id uuid NOT NULL,
  subtipo text NOT NULL,
  CONSTRAINT activo_subtipos_pkey PRIMARY KEY (activo_id, subtipo),
  CONSTRAINT activo_subtipos_activo_id_fkey FOREIGN KEY (activo_id) REFERENCES public.activos(id)
);
CREATE TABLE public.dependencias (
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  activo_superior_id uuid NOT NULL,
  activo_inferior_id uuid NOT NULL,
  grado numeric NOT NULL CHECK (grado >= 0::numeric AND grado <= 100::numeric),
  justificacion text,
  created_at timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT dependencias_pkey PRIMARY KEY (id),
  CONSTRAINT dependencias_activo_superior_id_fkey FOREIGN KEY (activo_superior_id) REFERENCES public.activos(id),
  CONSTRAINT dependencias_activo_inferior_id_fkey FOREIGN KEY (activo_inferior_id) REFERENCES public.activos(id)
);
CREATE TABLE public.personas_asociadas (
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  activo_id uuid NOT NULL,
  persona_nombre text NOT NULL,
  tipo_rol USER-DEFINED NOT NULL,
  grado numeric NOT NULL CHECK (grado >= 0::numeric AND grado <= 100::numeric),
  justificacion text,
  created_at timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT personas_asociadas_pkey PRIMARY KEY (id),
  CONSTRAINT personas_asociadas_activo_id_fkey FOREIGN KEY (activo_id) REFERENCES public.activos(id)
);
CREATE TABLE public.categorias_amenaza (
  id integer NOT NULL DEFAULT nextval('categorias_amenaza_id_seq'::regclass),
  nombre text NOT NULL UNIQUE,
  CONSTRAINT categorias_amenaza_pkey PRIMARY KEY (id)
);
CREATE TABLE public.catalogo_amenazas (
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  amenaza text NOT NULL,
  categoria_id integer,
  probabilidad USER-DEFINED NOT NULL,
  subcategoria text,
  degradacion_d numeric CHECK (degradacion_d >= 0::numeric AND degradacion_d <= 100::numeric),
  degradacion_i numeric CHECK (degradacion_i >= 0::numeric AND degradacion_i <= 100::numeric),
  degradacion_c numeric CHECK (degradacion_c >= 0::numeric AND degradacion_c <= 100::numeric),
  degradacion_a numeric CHECK (degradacion_a >= 0::numeric AND degradacion_a <= 100::numeric),
  degradacion_t numeric CHECK (degradacion_t >= 0::numeric AND degradacion_t <= 100::numeric),
  version text,
  vigente boolean NOT NULL DEFAULT true,
  created_at timestamp with time zone NOT NULL DEFAULT now(),
  updated_at timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT catalogo_amenazas_pkey PRIMARY KEY (id),
  CONSTRAINT catalogo_amenazas_categoria_id_fkey FOREIGN KEY (categoria_id) REFERENCES public.categorias_amenaza(id)
);
CREATE TABLE public.amenaza_tipo_activo (
  amenaza_id uuid NOT NULL,
  tipo_activo USER-DEFINED NOT NULL,
  CONSTRAINT amenaza_tipo_activo_pkey PRIMARY KEY (amenaza_id, tipo_activo),
  CONSTRAINT amenaza_tipo_activo_amenaza_id_fkey FOREIGN KEY (amenaza_id) REFERENCES public.catalogo_amenazas(id)
);
CREATE TABLE public.subtipos_catalogo (
  subtipo text NOT NULL,
  tipo_magerit USER-DEFINED NOT NULL,
  created_at timestamp with time zone NOT NULL DEFAULT now(),
  subcategoria_amenaza text,
  CONSTRAINT subtipos_catalogo_pkey PRIMARY KEY (subtipo, tipo_magerit)
);
CREATE TABLE public.proyectos (
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  nombre text NOT NULL UNIQUE,
  sector text,
  tamano text,
  descripcion text,
  created_at timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT proyectos_pkey PRIMARY KEY (id)
);