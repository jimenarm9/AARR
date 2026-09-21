-- 1. Tipo de familia de madurez
CREATE TYPE tipo_familia_madurez AS ENUM ('Automatizada', 'Semi', 'Manual');

-- 2. Familia por defecto en el catalogo de salvaguardas
ALTER TABLE salvaguardas ADD COLUMN familia tipo_familia_madurez;

UPDATE salvaguardas SET familia = 'Automatizada' WHERE nombre IN ('[op.acc.5] Mecanismo de autenticacion (usuarios externos)', '[op.acc.6] Mecanismo de autenticacion (usuarios internos)', '[op.exp.2] Configuracion de seguridad', '[op.exp.6] Proteccion frente a codigo danino', '[op.exp.10] Proteccion de claves criptograficas', '[mp.if.4] Energia electrica', '[mp.if.5] Proteccion frente a incendios', '[mp.if.6] Proteccion frente a inundaciones', '[mp.eq.2] Bloqueo de puesto de trabajo', '[mp.com.1] Perimetro seguro', '[mp.com.2] Proteccion de la confidencialidad', '[mp.com.3] Proteccion de la integridad y de la autenticidad', '[mp.com.4] Separacion de flujos de informacion en la red', '[mp.si.2] Criptografia', '[mp.info.3] Cifrado de la informacion', '[mp.info.4] Firma electronica', '[mp.info.5] Sellos de tiempo', '[mp.info.9] Copias de seguridad (backup)', '[mp.s.1] Proteccion del correo electronico', '[mp.s.2] Proteccion de servicios y aplicaciones web', '[mp.s.3] Proteccion de la navegacion web', '[mp.s.4] Proteccion frente a la denegacion de servicio', '[op.mon.1] Deteccion de intrusion');

UPDATE salvaguardas SET familia = 'Semi' WHERE nombre IN ('[op.acc.1] Identificacion', '[op.acc.4] Gestion de derechos de acceso', '[op.exp.1] Inventario de activos', '[op.exp.3] Gestion de la configuracion de seguridad', '[op.exp.4] Mantenimiento y actualizaciones de seguridad', '[op.exp.7] Gestion de incidentes', '[op.ext.4] Interconexion de sistemas', '[op.nub.1] Proteccion de servicios en la nube', '[op.cont.4] Medios alternativos', '[mp.if.1] Areas separadas y con control de acceso', '[mp.eq.3] Proteccion de dispositivos portatiles', '[mp.eq.4] Otros dispositivos conectados a la red', '[mp.si.5] Borrado y destruccion', '[mp.sw.1] Desarrollo de aplicaciones', '[mp.info.6] Limpieza de documentos publicados', '[op.pl.4] Dimensionamiento/gestion de la capacidad', '[op.exp.8] Registro de la actividad', '[op.mon.2] Sistema de metricas', '[op.mon.3] Vigilancia');

UPDATE salvaguardas SET familia = 'Manual' WHERE nombre IN ('[op.acc.2] Requisitos de acceso', '[op.acc.3] Segregacion de funciones y tareas', '[op.exp.5] Gestion de cambios', '[op.exp.9] Registro de la gestion de incidentes', '[op.ext.1] Contratacion y acuerdos de nivel de servicio', '[op.ext.2] Gestion diaria', '[op.ext.3] Proteccion de la cadena de suministro', '[op.cont.1] Analisis de impacto', '[op.cont.2] Plan de continuidad', '[op.cont.3] Pruebas periodicas', '[mp.if.2] Identificacion de las personas', '[mp.if.3] Acondicionamiento de los locales', '[mp.if.7] Registro de entrada y salida de equipamiento', '[mp.per.1] Caracterizacion del puesto de trabajo', '[mp.per.2] Deberes y obligaciones', '[mp.eq.1] Puesto de trabajo despejado', '[mp.si.1] Marcado de soportes', '[mp.si.3] Custodia', '[mp.si.4] Transporte', '[mp.sw.2] Aceptacion y puesta en servicio', '[mp.info.1] Datos de caracter personal', '[mp.info.2] Calificacion de la informacion', '[org.1] Politica de seguridad', '[org.2] Normativa de seguridad', '[org.3] Procedimientos de seguridad', '[org.4] Proceso de autorizacion', '[op.pl.1] Analisis de riesgos', '[op.pl.2] Arquitectura de seguridad', '[op.pl.3] Adquisicion de nuevos componentes', '[op.pl.5] Componentes certificados', '[mp.per.3] Concienciacion', '[mp.per.4] Formacion');

-- 3. Nuevas columnas en activo_salvaguardas
ALTER TABLE activo_salvaguardas
  ADD COLUMN familia_override tipo_familia_madurez,  -- NULL = usa la familia del catálogo
  ADD COLUMN nivel_madurez SMALLINT NOT NULL DEFAULT 5 CHECK (nivel_madurez BETWEEN 0 AND 5),
  ADD COLUMN coverage NUMERIC(5,2) NOT NULL DEFAULT 100 CHECK (coverage BETWEEN 0 AND 100),
  ADD COLUMN reliability NUMERIC(5,2) NOT NULL DEFAULT 100 CHECK (reliability BETWEEN 0 AND 100);

-- 4. Eliminar la columna y el tipo antiguos (madurez L0-L5 lineal, ya sustituido)
ALTER TABLE activo_salvaguardas DROP COLUMN madurez;
DROP TYPE nivel_madurez;
