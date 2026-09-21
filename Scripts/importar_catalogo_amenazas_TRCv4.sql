-- Importación del catálogo de amenazas TRCv4
-- Generado a partir de catalogo_amenazas_TRCv4.xlsx
-- Destino:
--   public.categorias_amenaza
--   public.catalogo_amenazas
--   public.amenaza_tipo_activo
--
-- Este script es reejecutable: evita duplicar categorías, amenazas y relaciones.
-- Las degradaciones D/I/C/A/T se cargan tal como están en el Excel (escala 0..100).
-- Las 18 amenazas de IA se conservan en catalogo_amenazas, pero NO se relacionan
-- en amenaza_tipo_activo porque la metodología actual no modela activos IA.
--
-- Recomendación: ejecutar el script completo en DBeaver.

BEGIN;

-- ============================================================
-- 1) CATEGORÍAS
-- ============================================================
WITH nuevas(nombre) AS (
    VALUES
    ('Amenazas de Inteligencia Artificial'),
    ('Amenazas físicas'),
    ('Amenazas internas (insider)'),
    ('Ataques a aplicaciones web'),
    ('Cadena de suministro / Terceros'),
    ('Cloud'),
    ('Comunicaciones / Red'),
    ('Criptográfico'),
    ('Denegación de Servicio'),
    ('Desastres'),
    ('Errores humanos / operacionales'),
    ('Exfiltración, fuga de información y espionaje'),
    ('Explotación de vulnerabilidades / Malware'),
    ('Manipulación e integridad de datos'),
    ('Phishing / Ingeniería social'),
    ('Ransomware / Extorsión'),
    ('Robo o perdida'),
    ('Robo y abuso de credenciales'),
    ('SCADA'),
    ('Trazabilidad y logs')
),
base AS (
    SELECT COALESCE(MAX(id), 0) AS max_id
    FROM public.categorias_amenaza
),
pendientes AS (
    SELECT n.nombre,
           b.max_id + ROW_NUMBER() OVER (ORDER BY n.nombre) AS nuevo_id
    FROM nuevas n
    CROSS JOIN base b
    WHERE NOT EXISTS (
        SELECT 1
        FROM public.categorias_amenaza c
        WHERE lower(trim(c.nombre)) = lower(trim(n.nombre))
    )
)
INSERT INTO public.categorias_amenaza (id, nombre)
OVERRIDING SYSTEM VALUE
SELECT nuevo_id, nombre
FROM pendientes;

-- Si la columna id usa una secuencia/identity, la dejamos sincronizada.
DO $$
DECLARE
    seq_name text;
    max_id_value bigint;
BEGIN
    seq_name := pg_get_serial_sequence('public.categorias_amenaza', 'id');
    IF seq_name IS NOT NULL THEN
        SELECT COALESCE(MAX(id), 1) INTO max_id_value
        FROM public.categorias_amenaza;
        PERFORM setval(seq_name::regclass, max_id_value, true);
    END IF;
END $$;

-- ============================================================
-- 2) CATÁLOGO DE AMENAZAS
-- ============================================================
WITH datos(
    id, amenaza, categoria, probabilidad, subcategoria,
    degradacion_d, degradacion_i, degradacion_c, degradacion_a, degradacion_t
) AS (
    VALUES
    ('5025cd0c-7037-5c72-85b1-aa6db8635830'::uuid, 'Denegación de servicio (DoS/DDoS)', 'Denegación de Servicio', 'Muy Alta', NULL, 90, 10, 10, 0, 10),
    ('d833eca4-c92b-5a80-814b-694ff08954fe'::uuid, 'Ransomware', 'Ransomware / Extorsión', 'Muy Alta', NULL, 80, 80, 25, 0, 0),
    ('77fa7d91-7058-5797-b1fe-dc2dbf56ec67'::uuid, 'Malware', 'Explotación de vulnerabilidades / Malware', 'Muy Alta', NULL, 50, 50, 50, 10, 10),
    ('5badad2b-9c33-59a2-9b2a-6eabdce5fecd'::uuid, 'Ingeniería social', 'Phishing / Ingeniería social', 'Muy Alta', NULL, 10, 25, 50, 0, 0),
    ('8375f9c5-cd5a-5eb8-a573-3880a1aa4a10'::uuid, 'Robo de credenciales', 'Robo y abuso de credenciales', 'Alta', NULL, 10, 25, 50, 50, 25),
    ('ad9a3aa0-9027-593e-b7b2-291ab3ba7bf2'::uuid, 'Ataque contra las credenciales', 'Robo y abuso de credenciales', 'Alta', NULL, 10, 25, 50, 50, 25),
    ('c9ba7b60-31fd-5b8d-9cf9-f229b35f2900'::uuid, 'Explotación de vulnerabilidades', 'Explotación de vulnerabilidades / Malware', 'Muy Alta', NULL, 50, 80, 50, 0, 0),
    ('5db7d5c4-8963-5463-a26e-7ac52a591ad1'::uuid, 'Sistemas sin parchear u obsoletos', 'Explotación de vulnerabilidades / Malware', 'Alta', NULL, 50, 50, 10, 0, 0),
    ('c63ba8de-8a08-51bd-83ec-f86e3d55afe3'::uuid, 'Configuración insegura', 'Explotación de vulnerabilidades / Malware', 'Media', 'Configuración', 50, 50, 50, 10, 10),
    ('53e49943-4cb1-5de2-9a3d-0abed287b28b'::uuid, 'Acceso no autorizado a la información', 'Exfiltración, fuga de información y espionaje', 'Media', NULL, 0, 0, 50, 0, 0),
    ('8dc86ddc-3978-5c44-8427-fbcfe6a25603'::uuid, 'Escalada de privilegios', 'Robo y abuso de credenciales', 'Alta', NULL, 20, 25, 80, 10, 10),
    ('d870388b-43b2-57d5-93fc-d23ad495b561'::uuid, 'Compromiso de cuentas privilegiadas', 'Robo y abuso de credenciales', 'Alta', NULL, 80, 80, 80, 50, 10),
    ('6d7e4fcc-88b0-5976-9e05-ec92d372bbd3'::uuid, 'Abuso de privilegios', 'Robo y abuso de credenciales', 'Media', NULL, 10, 50, 80, 25, 0),
    ('a182b324-bc4e-5a57-a431-5002ab484c91'::uuid, 'Exfiltración de información', 'Exfiltración, fuga de información y espionaje', 'Alta', NULL, 0, 0, 100, 0, 0),
    ('e04e280a-20e6-5969-abbd-156fd06e6039'::uuid, 'Robo de información', 'Exfiltración, fuga de información y espionaje', 'Media', NULL, 10, 0, 100, 0, 25),
    ('48a618da-59de-5f60-a082-99133ccfb726'::uuid, 'Borrado o destrucción intencionada de información', 'Manipulación e integridad de datos', 'Media', NULL, 100, 100, 0, 0, 0),
    ('3c40eff3-a714-503a-a113-5058dceb9622'::uuid, 'Pérdida o corrupción de información', 'Manipulación e integridad de datos', 'Media', NULL, 25, 80, 10, 0, 10),
    ('f4eedc3c-8b35-56ac-a61d-52644cc4f734'::uuid, 'Compromiso de correo electrónico (BEC)', 'Phishing / Ingeniería social', 'Alta', NULL, 10, 25, 50, 0, 0),
    ('e9998683-17ff-5fb5-8834-bed3ac7c0d01'::uuid, 'Suplantación de identidad del usuario', 'Phishing / Ingeniería social', 'Alta', NULL, 10, 25, 50, 50, 10),
    ('748d2b46-02cf-54b1-a3a1-a59de5afdd89'::uuid, 'Abuso del correo como vector de malware', 'Phishing / Ingeniería social', 'Alta', 'Correo', 80, 80, 80, 20, 10),
    ('8183a834-3786-516e-8ba6-f92540de6c20'::uuid, 'Spam / abuso de recursos', 'Phishing / Ingeniería social', 'Baja', 'Correo', 50, 0, 0, 0, 0),
    ('dd04fdf5-182b-5de1-ab21-adc4f3fdceee'::uuid, 'Relay de correo no autorizado', 'Comunicaciones / Red', 'Baja', 'Correo', 25, 10, 50, 10, 10),
    ('430ae0d4-edf8-5b2a-a535-1b05d6762f99'::uuid, 'Web defacement', 'Ataques a aplicaciones web', 'Media', 'Web', 25, 50, 10, 0, 0),
    ('2efb7862-6db9-5f5b-9165-3ae35dd93015'::uuid, 'Inyección SQL', 'Ataques a aplicaciones web', 'Alta', 'Web', 25, 50, 80, 0, 0),
    ('c2a91bda-d982-5114-895c-82ad5fa67f8d'::uuid, 'Cross-Site Scripting (XSS)', 'Ataques a aplicaciones web', 'Media', 'Web', 10, 50, 25, 0, 0),
    ('1cb59f7a-5345-5411-b11e-c23cf0a417bd'::uuid, 'CSRF / acciones no autorizadas', 'Ataques a aplicaciones web', 'Media', 'Web', 10, 50, 30, 10, 10),
    ('c7056c5d-5882-5e2f-a612-9b1868e7829b'::uuid, 'Ataques contra APIs', 'Ataques a aplicaciones web', 'Media', 'Web', 50, 50, 50, 0, 0),
    ('0f0ef7c6-8e6b-5fe3-80ef-64d54fb5e7ae'::uuid, 'Secuestro de sesión', 'Robo y abuso de credenciales', 'Media', 'Web', 10, 30, 50, 50, 0),
    ('e926f6b9-594d-5a2a-9f43-9042691ae489'::uuid, 'Ataques de cadena de suministro', 'Cadena de suministro / Terceros', 'Muy Alta', 'Subcontratado', 50, 50, 50, 0, 0),
    ('b78e809c-6653-5d76-86ac-f1b813afbceb'::uuid, 'Dependencia de proveedor como punto único de fallo', 'Cadena de suministro / Terceros', 'Media', 'Subcontratado', 80, 0, 0, 0, 0),
    ('3252beb5-fd02-5c23-90c1-5771a635bf09'::uuid, 'Kerberoasting', 'Robo y abuso de credenciales', 'Alta', 'Dominio', 0, 25, 50, 50, 10),
    ('b168a461-d87a-5dbf-8fe1-fecf37eb48a8'::uuid, 'AS-REP Roasting', 'Robo y abuso de credenciales', 'Media', 'Dominio', 0, 25, 50, 50, 10),
    ('c7234a9d-4c40-5562-bddb-71f5e0a424c7'::uuid, 'Persistencia y exfiltración de credenciales del dominio (DCSync / Golden Ticket / DCShadow / NTDS.dit)', 'Robo y abuso de credenciales', 'Media', 'Dominio', 0, 50, 80, 80, 25),
    ('8da3b183-dc45-5b25-b0dc-a07929c6c06d'::uuid, 'Abuso de delegación Kerberos', 'Robo y abuso de credenciales', 'Media', 'Dominio', 0, 10, 25, 50, 10),
    ('81998657-0c1f-5ba9-b488-89fd5af561c8'::uuid, 'Reglas del Firewall mal configuradas', 'Errores humanos / operacionales', 'Media', 'Firewall', 50, 50, 50, 0, 0),
    ('6b1857be-b423-5cf3-80bc-165db8b06521'::uuid, 'Exfiltración masiva mediante consultas o exportaciones no autorizadas', 'Exfiltración, fuga de información y espionaje', 'Media', 'BBDD', 80, 0, 0, 0, 0),
    ('7f16e1ce-33df-53d3-b523-09a1131cf752'::uuid, 'Software malicioso de terceros', 'Cadena de suministro / Terceros', 'Media', 'Subcontratado', 50, 50, 50, 10, 10),
    ('88468084-d145-522d-9930-f0168a233233'::uuid, 'Actualizaciones de software comprometidas', 'Cadena de suministro / Terceros', 'Media', 'Subcontratado', 0, 100, 0, 0, 0),
    ('cba35a89-fbfe-5c03-8e50-0ef84b16a6ae'::uuid, 'Cryptojacking', 'Explotación de vulnerabilidades / Malware', 'Media', NULL, 50, 0, 0, 0, 0),
    ('ef551c8e-9467-5739-945d-e798298aed48'::uuid, 'Comando y control (C2)', 'Explotación de vulnerabilidades / Malware', 'Media', NULL, 0, 0, 80, 0, 0),
    ('229fcbbb-5ad2-5f4a-9536-e16ceeaaefad'::uuid, 'Persistencia mediante cuentas o servicios legítimos', 'Robo y abuso de credenciales', 'Media', NULL, 10, 25, 50, 10, 25),
    ('2969cb68-fed9-5de6-b4b1-f697bf7e40d1'::uuid, 'Explotación de servicios expuestos a Internet', 'Explotación de vulnerabilidades / Malware', 'Alta', NULL, 50, 50, 50, 0, 0),
    ('44412af1-157f-5ce0-b331-2831db831884'::uuid, 'Intercepción de comunicaciones', 'Comunicaciones / Red', 'Media', NULL, 10, 10, 80, 0, 0),
    ('a8b68705-4460-5288-a0c5-2394b5c71d9d'::uuid, 'Suplantación del servicio', 'Comunicaciones / Red', 'Media', NULL, 10, 10, 25, 50, 10),
    ('27a6c2e2-b395-5136-806b-ad1696df212a'::uuid, 'Secuestro de dominio', 'Comunicaciones / Red', 'Media', 'Web', 25, 25, 25, 50, 25),
    ('9f035bc5-071b-570c-b743-eca1f8940246'::uuid, 'Manipulación de registros DNS', 'Comunicaciones / Red', 'Media', 'DNS', 10, 80, 25, 50, 10),
    ('4dc38550-1813-5afb-9bea-dbb6c3d09745'::uuid, 'Robo o pérdida de certificados / claves privadas', 'Criptográfico', 'Media', 'Certificados', 10, 25, 50, 50, 25),
    ('89e57a7c-182a-5229-9229-405d7eed2797'::uuid, 'Fatiga de MFA / MFA bombing', 'Robo y abuso de credenciales', 'Media', 'Doble factor', 10, 25, 50, 25, 10),
    ('49d0adae-56ab-572c-ab9e-d85951db72ce'::uuid, 'Fallo de hardware', 'Amenazas físicas', 'Media', NULL, 80, 25, 0, 0, 10),
    ('8f35a49c-8254-5c88-b6b3-44fc64230ac4'::uuid, 'Fallo de software', 'Cadena de suministro / Terceros', 'Media', NULL, 80, 50, 0, 0, 0),
    ('8f2be361-d1a1-5198-8278-e784ed1e38d7'::uuid, 'Fallo de suministro eléctrico', 'Amenazas físicas', 'Media', NULL, 80, 10, 10, 0, 0),
    ('11a390ec-5875-5874-b164-84f8b8232cf5'::uuid, 'Fallo de climatización', 'Amenazas físicas', 'Baja', NULL, 80, 10, 10, 0, 0),
    ('4c3be4ce-73b7-598e-a6f1-3933d0651520'::uuid, 'Incendio', 'Amenazas físicas', 'Muy Baja', NULL, 80, 10, 10, 0, 0),
    ('718eb463-eaa4-59ff-b37b-cfc4df8dc89c'::uuid, 'Inundación / daños por agua', 'Amenazas físicas', 'Muy Baja', NULL, 80, 10, 10, 0, 0),
    ('0bf6a7fc-8c66-5825-9e16-897817808d67'::uuid, 'Desastre natural', 'Desastres', 'Muy Baja', NULL, 100, 0, 0, 0, 0),
    ('7c347be5-e982-574e-860d-e8ada8967783'::uuid, 'Robo de equipos', 'Robo o perdida', 'Media', 'Movil', 100, 0, 80, 0, 25),
    ('ebe61f39-3b2d-5938-acfa-db85dd548343'::uuid, 'Acceso físico no autorizado', 'Amenazas físicas', 'Baja', NULL, 10, 10, 50, 0, 0),
    ('852a0add-d3c3-579f-ab1b-a0438b9dbf11'::uuid, 'Pérdida de soportes de información', 'Robo o perdida', 'Media', 'Movil', 100, 0, 50, 0, 25),
    ('61b314dd-07f0-5c67-a3a8-1e3d4eeacdda'::uuid, 'Copias de seguridad comprometidas o cifradas', 'Ransomware / Extorsión', 'Alta', 'Backup', 80, 50, 0, 0, 0),
    ('1e292c13-00cb-5af0-860e-5545b8c55bd0'::uuid, 'Fallo en la ejecución del backup', 'Errores humanos / operacionales', 'Media', 'Backup', 25, 0, 0, 0, 0),
    ('486d8dd2-b423-526d-99e0-8c2084b7f17b'::uuid, 'Error en la restauración de las copias de seguridad', 'Errores humanos / operacionales', 'Media', 'Backup', 50, 0, 0, 0, 0),
    ('be9e2ceb-fc5d-5c5b-adf9-e8bd15c73261'::uuid, 'Fallo de recuperación ante desastres', 'Errores humanos / operacionales', 'Baja', NULL, 80, 0, 0, 0, 0),
    ('2c67fc1e-ddf6-58af-b6f6-e2af5f96dfbf'::uuid, 'Manipulación de logs o registros', 'Trazabilidad y logs', 'Media', 'Logs', 10, 80, 10, 10, 80),
    ('cf99b130-48cf-5475-b879-1ad92213f491'::uuid, 'Pérdida de trazabilidad', 'Trazabilidad y logs', 'Media', NULL, 10, 25, 10, 10, 80),
    ('057c8e53-e591-5302-9b05-1208a23254be'::uuid, 'Insider', 'Amenazas internas (insider)', 'Alta', NULL, 80, 80, 80, 0, 0),
    ('883d7429-4dee-551d-998b-dcb24a32b658'::uuid, 'Espionaje industrial', 'Exfiltración, fuga de información y espionaje', 'Media', 'Confidencial', 0, 0, 100, 0, 0),
    ('64e967e2-c3d9-5bfc-878f-babae6e60a9e'::uuid, 'Manipulación intencionada de la información', 'Manipulación e integridad de datos', 'Media', NULL, 10, 80, 25, 10, 0),
    ('f97470b1-a17f-5b57-bc25-487639357f34'::uuid, 'Uso malicioso de IA generativa', 'Amenazas de Inteligencia Artificial', 'Alta', NULL, 50, 50, 50, 0, 0),
    ('ca9c841f-176a-5069-8a0a-4e1af9a02a24'::uuid, 'Desastres industriales (otros)', 'Amenazas físicas', 'Muy Baja', NULL, 100, 0, 0, 0, 0),
    ('41238606-bf75-5a48-bb44-02eddac57a9a'::uuid, 'Contaminación mecánica', 'Amenazas físicas', 'Muy Baja', NULL, 50, 0, 0, 0, 0),
    ('de21ee41-261b-592d-8c61-43b8f3988cff'::uuid, 'Contaminación electromagnética', 'Amenazas físicas', 'Muy Baja', NULL, 25, 0, 0, 0, 0),
    ('9e3c0365-7631-5648-a125-215e8caab953'::uuid, 'Condiciones inadecuadas de temperatura o humedad', 'Amenazas físicas', 'Muy Baja', NULL, 100, 0, 0, 0, 0),
    ('1a4e1f7c-cd2f-5222-ba5a-a9b90403fae3'::uuid, 'Fallo de servicios de comunicaciones', 'Amenazas físicas', 'Media', 'Comunicaciones', 100, 0, 0, 0, 0),
    ('0bbe9101-b226-5526-8dd5-8e80c64465fb'::uuid, 'Interrupción de la cadena de suministros', 'Cadena de suministro / Terceros', 'Media', 'Subcontratado', 50, 0, 0, 0, 0),
    ('d2d0fd4a-86f4-516f-b767-17a0262f1384'::uuid, 'Degradación de soportes de almacenamiento', 'Amenazas físicas', 'Baja', 'Almacenamiento', 50, 0, 0, 0, 0),
    ('1d2aea70-cf72-5a48-8099-475e75c07808'::uuid, 'Emanaciones electromagnéticas', 'Comunicaciones / Red', 'Baja', NULL, 0, 0, 50, 0, 0),
    ('6349db44-86e4-5d6c-a8f1-659d3ce04c6a'::uuid, 'Errores no intencionados de los usuarios', 'Errores humanos / operacionales', 'Media', NULL, 10, 50, 20, 0, 0),
    ('a0dc6e5f-e241-53d4-b419-1ac94db30e2b'::uuid, 'Errores no intencionadas del administrador', 'Errores humanos / operacionales', 'Media', NULL, 25, 80, 50, 0, 0),
    ('534ae59a-9d31-5698-9bd1-820b7a7d8671'::uuid, 'Errores de monitorización (log)', 'Trazabilidad y logs', 'Media', 'Logs', 0, 80, 0, 0, 80),
    ('3eb8b91a-d300-5c0f-982e-ffda71fd3f65'::uuid, 'Difusión de software dañino', 'Explotación de vulnerabilidades / Malware', 'Media', NULL, 0, 50, 50, 10, 0),
    ('1fc84715-6c31-5817-969b-e2354c9dfa46'::uuid, 'Alteración accidental de la información', 'Manipulación e integridad de datos', 'Media', NULL, 10, 50, 25, 10, 0),
    ('c68a4d01-a017-52a4-9e3f-4bdb541102c9'::uuid, 'Fugas de información (no intencionadas)', 'Exfiltración, fuga de información y espionaje', 'Media', NULL, 0, 0, 50, 0, 0),
    ('2f5c5d40-53eb-540b-b857-24d194361a6c'::uuid, 'Errores de mantenimiento / actualización de programas', 'Errores humanos / operacionales', 'Media', NULL, 50, 50, 0, 0, 0),
    ('d6587e08-63b1-5a2b-be73-ea6236f27ce8'::uuid, 'Errores de mantenimiento de equipos', 'Errores humanos / operacionales', 'Media', NULL, 50, 50, 0, 0, 0),
    ('578fa27c-9437-5eed-acbe-41679c65bf4b'::uuid, 'Caída del sistema por agotamiento de recursos', 'Amenazas físicas', 'Media', NULL, 50, 0, 0, 0, 0),
    ('c4648d2e-9a11-54fc-bd0c-72007e54a5ae'::uuid, 'Pérdida de equipos', 'Robo o perdida', 'Baja', 'Movil', 100, 0, 25, 0, 25),
    ('6ffb64f9-13aa-5c76-8a3a-d89bd8c4c1e0'::uuid, 'Indisponibilidad del personal', 'Amenazas internas (insider)', 'Baja', NULL, 50, 0, 0, 0, 0),
    ('3d18280a-5550-53f0-987d-7a1cfa2b1c48'::uuid, 'Manipulación de la configuración', 'Manipulación e integridad de datos', 'Media', 'Configuración', 30, 90, 30, 0, 0),
    ('a6acf839-59c0-5ba7-8683-37a92deb7488'::uuid, 'Uso no previsto', 'Errores humanos / operacionales', 'Baja', NULL, 25, 25, 25, 0, 0),
    ('dbd090ad-8f73-5c90-a077-f1102793cd23'::uuid, 'Re-encaminamiento de mensajes', 'Comunicaciones / Red', 'Baja', NULL, 25, 50, 25, 10, 25),
    ('2f602442-4fb8-5ae0-8256-6825b9ca5093'::uuid, 'Alteración de secuencia', 'Comunicaciones / Red', 'Baja', NULL, 25, 50, 25, 10, 25),
    ('b4dedc3f-fb64-5d4e-85e5-72f68d0be464'::uuid, 'Manipulación de programas', 'Manipulación e integridad de datos', 'Media', NULL, 25, 80, 10, 10, 10),
    ('d6daa967-9e67-563d-bd32-49c22a542cf6'::uuid, 'Manipulación de los equipos (Hardware)', 'Manipulación e integridad de datos', 'Baja', NULL, 25, 80, 25, 10, 10),
    ('a553b94c-3303-50c9-b869-54e02c7259e7'::uuid, 'Ataque destructivo (sabotaje físico)', 'Amenazas físicas', 'Muy Baja', NULL, 100, 0, 0, 0, 0),
    ('1c0eaacb-7f43-5ee4-87b1-60aea79431b3'::uuid, 'Ocupación enemiga / Coerción', 'Amenazas físicas', 'Muy Baja', NULL, 50, 0, 25, 0, 0),
    ('e0d18f91-d982-51f6-833f-e2bc489c0758'::uuid, 'Extorsión (ransomware / data leak)', 'Ransomware / Extorsión', 'Alta', NULL, 25, 50, 80, 0, 0),
    ('910689d5-d88f-55ea-ac18-27699c31afb7'::uuid, 'Pérdida de información en cloud', 'Cloud', 'Media', 'Cloud', 0, 0, 80, 0, 0),
    ('39ff124a-99ab-51ae-be1c-03da8e6517ce'::uuid, 'Responsabilidad compartida mal delimitada', 'Cloud', 'Baja', 'Subcontratado', 25, 25, 25, 0, 0),
    ('e5268a9c-523c-5b80-851a-9fa6dd90b16c'::uuid, 'Vendor lock-in / Dependencia del proveedor', 'Cloud', 'Baja', 'Subcontratado', 25, 0, 0, 0, 0),
    ('011cf025-fc3f-516c-ab55-79c44fd4eedc'::uuid, 'Error en el aislamiento del servicio', 'Cloud', 'Media', 'Cloud', 50, 50, 50, 0, 0),
    ('c976ba2f-b817-5b5f-ad05-7239c1d0b249'::uuid, 'Cambio de jurisdicción', 'Cloud', 'Media', 'Cloud', 50, 0, 50, 0, 0),
    ('83f31d00-f953-59ba-bca9-e620a2957d39'::uuid, 'Error en la gestión de los datos', 'Cloud', 'Media', 'Cloud', 0, 0, 50, 0, 0),
    ('c6070ad9-46c8-5e3e-a855-fb86dfd27e16'::uuid, 'Perdida de la Gobernanza', 'Cloud', 'Alta', 'Cloud', 100, 100, 100, 0, 0),
    ('7be21eba-9402-5ca1-9323-cffa27889610'::uuid, 'Fallo en gestión de crisis y continuidad', 'Errores humanos / operacionales', 'Baja', NULL, 50, 0, 0, 0, 0),
    ('cd720902-d56e-526b-88ef-282e5ad0f90f'::uuid, 'Ransomware-as-a-Service (RaaS)', 'Ransomware / Extorsión', 'Alta', NULL, 80, 80, 80, 50, 50),
    ('6ec44ce2-97d6-5b40-bd8c-0560b295a49a'::uuid, 'Ataques a modelos de IA/ML', 'Amenazas de Inteligencia Artificial', 'Media', NULL, 25, 80, 25, 10, 25),
    ('c3fb14b3-e4da-51b1-a03c-e1a50183d34b'::uuid, 'Uso no autorizado de herramientas de IA (Shadow AI)', 'Amenazas de Inteligencia Artificial', 'Muy Alta', 'IA', 0, 0, 50, 0, 25),
    ('889d8dd8-6e54-56cd-b49e-0edfdbc373d8'::uuid, 'Introducción de información confidencial en una IA externa', 'Amenazas de Inteligencia Artificial', 'Muy Alta', 'Confidencial', 0, 0, 80, 0, 0),
    ('0942efce-fd3e-5896-9cb3-4cef9e216cf2'::uuid, 'Fuga de información mediante prompts', 'Amenazas de Inteligencia Artificial', 'Alta', 'IA', 0, 0, 50, 0, 0),
    ('e7e793e9-0321-5643-bdcf-8c5b035ca587'::uuid, 'Uso de datos corporativos para entrenamiento de modelos externos', 'Amenazas de Inteligencia Artificial', 'Media', 'IA', 0, 0, 25, 0, 25),
    ('d53c5321-f9a8-52a2-9acf-9d7c1f16c944'::uuid, 'Prompt injection', 'Amenazas de Inteligencia Artificial', 'Alta', 'IA', 25, 50, 50, 25, 25),
    ('13f0036b-99b0-5b74-98b2-00ac81254527'::uuid, 'Indirect prompt injection', 'Amenazas de Inteligencia Artificial', 'Media', 'IA', 25, 50, 80, 25, 25),
    ('a5e0afb1-e982-5fba-bcec-85c5aafbdbb4'::uuid, 'Envenenamiento de datos de la IA (Data Poisoning)', 'Amenazas de Inteligencia Artificial', 'Media', 'IA', 25, 80, 50, 25, 25),
    ('1c2d7029-f178-56c9-8425-b8e12b94dfa3'::uuid, 'Envenenamiento del modelo IA (Model Poisoning)', 'Amenazas de Inteligencia Artificial', 'Media', 'IA', 25, 80, 50, 30, 25),
    ('7384ec7a-6dd7-5658-904e-48ef5c51e78f'::uuid, 'Robo o extracción del modelo', 'Amenazas de Inteligencia Artificial', 'Media', 'IA', 25, 50, 80, 25, 25),
    ('846825c0-0dc1-53f7-95c9-27c6e64bd7c4'::uuid, 'Robo de prompts internos / información de contexto', 'Amenazas de Inteligencia Artificial', 'Media', 'IA', 25, 50, 80, 25, 25),
    ('a8e6a5f5-c49d-51a0-acc0-f075dc98253c'::uuid, 'Generación de código inseguro mediante IA', 'Amenazas de Inteligencia Artificial', 'Alta', 'Desarrollo', 50, 80, 50, 25, 10),
    ('07ff424e-a266-5ae1-8ac2-cd3d3259dcdf'::uuid, 'Introducción de vulnerabilidades en software generado por IA', 'Amenazas de Inteligencia Artificial', 'Alta', 'Desarrollo', 50, 80, 50, 0, 0),
    ('431f2173-6ecf-5fdf-81d2-e2c4cf6741de'::uuid, 'Decisiones empresariales incorrectas basadas en IA', 'Amenazas de Inteligencia Artificial', 'Alta', 'IA', 25, 80, 25, 50, 25),
    ('710098cb-fb5e-5e66-b1c4-2b97b538b7d5'::uuid, 'Agente IA con permisos excesivos', 'Amenazas de Inteligencia Artificial', 'Media', 'IA', 50, 80, 80, 25, 25),
    ('3312f1d4-0191-5101-b79f-6bc941b829a0'::uuid, 'Compromiso de agente IA', 'Amenazas de Inteligencia Artificial', 'Media', 'IA', 50, 50, 50, 10, 10),
    ('c44e9fc8-b600-5bde-aad0-7a4f9a17ba3e'::uuid, 'Uso indebido de herramientas conectadas a IA', 'Amenazas de Inteligencia Artificial', 'Media', 'IA', 50, 50, 80, 25, 25),
    ('1a1d878e-3d64-5770-a65e-5f4e2dc02619'::uuid, 'Dependencia excesiva de IA', 'Amenazas de Inteligencia Artificial', 'Alta', 'IA', 50, 0, 0, 0, 0),
    ('59f88bea-3cbb-5549-95b0-84c47ba4e444'::uuid, 'Ausencia de control de acceso a IA', 'Amenazas de Inteligencia Artificial', 'Alta', 'IA', 0, 50, 50, 0, 0),
    ('428efbed-4b66-5743-b09e-368e143f24a8'::uuid, 'Ausencia de trazabilidad de uso de IA', 'Amenazas de Inteligencia Artificial', 'Alta', 'IA', 0, 0, 0, 0, 50),
    ('283ce5fc-a8b1-5d7f-b30a-f2db8418a657'::uuid, 'Uso de IA sin supervisión humana', 'Amenazas de Inteligencia Artificial', 'Alta', 'IA', 0, 50, 50, 0, 0),
    ('029a7238-a949-5d60-adf4-b1982a5cd1bb'::uuid, 'Pérdida de control sobre datos enviados a IA SaaS', 'Amenazas de Inteligencia Artificial', 'Alta', 'IA', 0, 0, 50, 0, 25),
    ('9d7028b0-9bf8-5588-bad1-c82cd4f40e00'::uuid, 'Infostealers (malware ladrón de credenciales)', 'Robo y abuso de credenciales', 'Alta', NULL, 10, 25, 50, 50, 0),
    ('5aa50e88-dbad-5d0b-9ee1-a6e9a43e3fb9'::uuid, 'Ciberespionaje dirigido (APT estatal)', 'Exfiltración, fuga de información y espionaje', 'Media', 'Confidencial', 0, 0, 80, 0, 0),
    ('683def3a-4718-569c-a598-b172e3303943'::uuid, 'Ataques a infraestructura OT/SCADA', 'SCADA', 'Media', 'SCADA', 80, 50, 25, 10, 10),
    ('9259148c-7ffe-58ee-80ee-dda3fc8accf7'::uuid, 'Riesgo criptográfico cuántico (cosechar ahora, descifrar después)', 'Criptográfico', 'Muy Baja', 'Criptografía', 0, 0, 80, 0, 0),
    ('e13ae4d3-a6a3-5e58-8b24-7e8cacb712a7'::uuid, 'Borrado accidental de información', 'Manipulación e integridad de datos', 'Media', NULL, 100, 100, 0, 0, 0),
    ('8db9021a-4851-52a6-8e9e-5e1a35ad761e'::uuid, 'Fuga del hipervisor (VM escape)', 'Explotación de vulnerabilidades / Malware', 'Baja', 'Maquina Virtual', 80, 80, 80, 25, 10),
    ('4eb52df2-7bdd-5cf4-8de2-cd6dbb245ef3'::uuid, 'Compromiso del hipervisor / plataforma de virtualización', 'Explotación de vulnerabilidades / Malware', 'Baja', 'Hipervisor', 100, 100, 100, 50, 50),
    ('98728c70-1b0b-5466-8016-d36860adb7ab'::uuid, 'Exposición de datos sensibles en snapshots o clones de VM', 'Exfiltración, fuga de información y espionaje', 'Media', 'Maquina Virtual', 0, 25, 50, 10, 10),
    ('3f8a78d0-3557-50b5-b085-c60721e2b946'::uuid, 'Proliferación descontrolada de VM (VM sprawl)', 'Errores humanos / operacionales', 'Media', 'Maquina Virtual', 50, 0, 0, 0, 10),
    ('d66828ef-04d6-51e9-b3dc-a57406abb61c'::uuid, 'Plantilla base (golden image) comprometida', 'Cadena de suministro / Terceros', 'Baja', 'Maquina Virtual', 0, 50, 50, 50, 0),
    ('f0ba8d19-54b7-58f7-b4f8-3e499d7aaa75'::uuid, 'Agotamiento de recursos compartidos (noisy neighbor)', 'Denegación de Servicio', 'Media', 'Maquina Virtual', 80, 0, 0, 0, 0),
    ('06e3e896-ed59-51fe-864b-3a15dc03fc00'::uuid, 'Compromiso de la consola de administración de virtualización', 'Robo y abuso de credenciales', 'Media', 'Hipervisor', 100, 100, 100, 25, 25),
    ('b5db8f0b-b273-533d-acab-cfb5f7bc1b5e'::uuid, 'Avería de origen Físico o Lógico', 'Amenazas físicas', 'Muy Baja', NULL, 50, 0, 0, 0, 0)
)
INSERT INTO public.catalogo_amenazas (
    id, amenaza, categoria_id, probabilidad, subcategoria,
    degradacion_d, degradacion_i, degradacion_c, degradacion_a, degradacion_t,
    version, vigente, created_at, updated_at
)
SELECT
    d.id,
    d.amenaza,
    cat.id,
    d.probabilidad::public.probabilidad_nivel,
    d.subcategoria,
    d.degradacion_d,
    d.degradacion_i,
    d.degradacion_c,
    d.degradacion_a,
    d.degradacion_t,
    'TRCv4',
    TRUE,
    NOW(),
    NOW()
FROM datos d
JOIN LATERAL (
    SELECT c.id
    FROM public.categorias_amenaza c
    WHERE lower(trim(c.nombre)) = lower(trim(d.categoria))
    ORDER BY c.id
    LIMIT 1
) cat ON TRUE
WHERE NOT EXISTS (
    SELECT 1
    FROM public.catalogo_amenazas ca
    WHERE lower(trim(ca.amenaza)) = lower(trim(d.amenaza))
);

-- ============================================================
-- 3) RELACIÓN AMENAZA -> TIPO DE ACTIVO
-- ============================================================
WITH relaciones(amenaza, tipo_activo_txt) AS (
    VALUES
    ('Denegación de servicio (DoS/DDoS)', 'SERVICIO'),
    ('Ransomware', 'SOFTWARE'),
    ('Malware', 'SOFTWARE'),
    ('Ingeniería social', 'PERSONAL'),
    ('Robo de credenciales', 'SERVICIO'),
    ('Ataque contra las credenciales', 'SERVICIO'),
    ('Explotación de vulnerabilidades', 'SOFTWARE'),
    ('Sistemas sin parchear u obsoletos', 'SOFTWARE'),
    ('Configuración insegura', 'SERVICIO'),
    ('Configuración insegura', 'SOFTWARE'),
    ('Acceso no autorizado a la información', 'INFORMACION'),
    ('Escalada de privilegios', 'SERVICIO'),
    ('Compromiso de cuentas privilegiadas', 'SERVICIO'),
    ('Abuso de privilegios', 'INFORMACION'),
    ('Exfiltración de información', 'INFORMACION'),
    ('Robo de información', 'INFORMACION'),
    ('Borrado o destrucción intencionada de información', 'INFORMACION'),
    ('Pérdida o corrupción de información', 'INFORMACION'),
    ('Compromiso de correo electrónico (BEC)', 'PERSONAL'),
    ('Suplantación de identidad del usuario', 'SERVICIO'),
    ('Abuso del correo como vector de malware', 'SERVICIO'),
    ('Spam / abuso de recursos', 'SERVICIO'),
    ('Relay de correo no autorizado', 'SERVICIO'),
    ('Web defacement', 'SERVICIO'),
    ('Inyección SQL', 'SERVICIO'),
    ('Cross-Site Scripting (XSS)', 'SERVICIO'),
    ('CSRF / acciones no autorizadas', 'SERVICIO'),
    ('Ataques contra APIs', 'SERVICIO'),
    ('Secuestro de sesión', 'SERVICIO'),
    ('Ataques de cadena de suministro', 'SERVICIO'),
    ('Dependencia de proveedor como punto único de fallo', 'SERVICIO'),
    ('Kerberoasting', 'SERVICIO'),
    ('AS-REP Roasting', 'SERVICIO'),
    ('Persistencia y exfiltración de credenciales del dominio (DCSync / Golden Ticket / DCShadow / NTDS.dit)', 'SERVICIO'),
    ('Abuso de delegación Kerberos', 'SERVICIO'),
    ('Reglas del Firewall mal configuradas', 'SERVICIO'),
    ('Exfiltración masiva mediante consultas o exportaciones no autorizadas', 'SERVICIO'),
    ('Software malicioso de terceros', 'SOFTWARE'),
    ('Actualizaciones de software comprometidas', 'SOFTWARE'),
    ('Cryptojacking', 'EQUIPAMIENTO'),
    ('Comando y control (C2)', 'SOFTWARE'),
    ('Persistencia mediante cuentas o servicios legítimos', 'SERVICIO'),
    ('Explotación de servicios expuestos a Internet', 'SERVICIO'),
    ('Intercepción de comunicaciones', 'COMUNICACIONES'),
    ('Suplantación del servicio', 'SERVICIO'),
    ('Secuestro de dominio', 'SERVICIO'),
    ('Manipulación de registros DNS', 'SERVICIO'),
    ('Robo o pérdida de certificados / claves privadas', 'SERVICIO'),
    ('Fatiga de MFA / MFA bombing', 'SERVICIO'),
    ('Fallo de hardware', 'EQUIPAMIENTO'),
    ('Fallo de software', 'SOFTWARE'),
    ('Fallo de suministro eléctrico', 'INSTALACIONES'),
    ('Fallo de suministro eléctrico', 'EQUIPAMIENTO'),
    ('Fallo de climatización', 'INSTALACIONES'),
    ('Incendio', 'INSTALACIONES'),
    ('Inundación / daños por agua', 'INSTALACIONES'),
    ('Desastre natural', 'INSTALACIONES'),
    ('Desastre natural', 'EQUIPAMIENTO'),
    ('Robo de equipos', 'EQUIPAMIENTO'),
    ('Acceso físico no autorizado', 'INSTALACIONES'),
    ('Pérdida de soportes de información', 'EQUIPAMIENTO'),
    ('Copias de seguridad comprometidas o cifradas', 'SERVICIO'),
    ('Fallo en la ejecución del backup', 'SERVICIO'),
    ('Error en la restauración de las copias de seguridad', 'SERVICIO'),
    ('Fallo de recuperación ante desastres', 'SERVICIO'),
    ('Manipulación de logs o registros', 'INFORMACION'),
    ('Pérdida de trazabilidad', 'INFORMACION'),
    ('Insider', 'PERSONAL'),
    ('Espionaje industrial', 'INFORMACION'),
    ('Manipulación intencionada de la información', 'INFORMACION'),
    ('Desastres industriales (otros)', 'INSTALACIONES'),
    ('Desastres industriales (otros)', 'EQUIPAMIENTO'),
    ('Contaminación mecánica', 'EQUIPAMIENTO'),
    ('Contaminación electromagnética', 'EQUIPAMIENTO'),
    ('Condiciones inadecuadas de temperatura o humedad', 'EQUIPAMIENTO'),
    ('Fallo de servicios de comunicaciones', 'EQUIPAMIENTO'),
    ('Interrupción de la cadena de suministros', 'SERVICIO'),
    ('Degradación de soportes de almacenamiento', 'EQUIPAMIENTO'),
    ('Emanaciones electromagnéticas', 'EQUIPAMIENTO'),
    ('Errores no intencionados de los usuarios', 'INFORMACION'),
    ('Errores no intencionados de los usuarios', 'SERVICIO'),
    ('Errores no intencionadas del administrador', 'INFORMACION'),
    ('Errores no intencionadas del administrador', 'SERVICIO'),
    ('Errores de monitorización (log)', 'INFORMACION'),
    ('Difusión de software dañino', 'SOFTWARE'),
    ('Alteración accidental de la información', 'INFORMACION'),
    ('Fugas de información (no intencionadas)', 'INFORMACION'),
    ('Errores de mantenimiento / actualización de programas', 'SOFTWARE'),
    ('Errores de mantenimiento de equipos', 'EQUIPAMIENTO'),
    ('Caída del sistema por agotamiento de recursos', 'EQUIPAMIENTO'),
    ('Pérdida de equipos', 'EQUIPAMIENTO'),
    ('Indisponibilidad del personal', 'PERSONAL'),
    ('Manipulación de la configuración', 'SERVICIO'),
    ('Manipulación de la configuración', 'SOFTWARE'),
    ('Uso no previsto', 'SERVICIO'),
    ('Re-encaminamiento de mensajes', 'COMUNICACIONES'),
    ('Alteración de secuencia', 'COMUNICACIONES'),
    ('Manipulación de programas', 'SOFTWARE'),
    ('Manipulación de los equipos (Hardware)', 'EQUIPAMIENTO'),
    ('Ataque destructivo (sabotaje físico)', 'INSTALACIONES'),
    ('Ocupación enemiga / Coerción', 'INSTALACIONES'),
    ('Extorsión (ransomware / data leak)', 'PERSONAL'),
    ('Pérdida de información en cloud', 'SERVICIO'),
    ('Responsabilidad compartida mal delimitada', 'SERVICIO'),
    ('Vendor lock-in / Dependencia del proveedor', 'SERVICIO'),
    ('Error en el aislamiento del servicio', 'SERVICIO'),
    ('Cambio de jurisdicción', 'SERVICIO'),
    ('Error en la gestión de los datos', 'SERVICIO'),
    ('Perdida de la Gobernanza', 'SERVICIO'),
    ('Fallo en gestión de crisis y continuidad', 'SERVICIO'),
    ('Ransomware-as-a-Service (RaaS)', 'INFORMACION'),
    ('Uso no autorizado de herramientas de IA (Shadow AI)', 'INFORMACION'),
    ('Introducción de información confidencial en una IA externa', 'INFORMACION'),
    ('Fuga de información mediante prompts', 'INFORMACION'),
    ('Uso de datos corporativos para entrenamiento de modelos externos', 'INFORMACION'),
    ('Pérdida de control sobre datos enviados a IA SaaS', 'INFORMACION'),
    ('Infostealers (malware ladrón de credenciales)', 'SOFTWARE'),
    ('Ciberespionaje dirigido (APT estatal)', 'INFORMACION'),
    ('Ataques a infraestructura OT/SCADA', 'EQUIPAMIENTO'),
    ('Riesgo criptográfico cuántico (cosechar ahora, descifrar después)', 'INFORMACION'),
    ('Borrado accidental de información', 'INFORMACION'),
    ('Fuga del hipervisor (VM escape)', 'SOFTWARE'),
    ('Compromiso del hipervisor / plataforma de virtualización', 'SOFTWARE'),
    ('Exposición de datos sensibles en snapshots o clones de VM', 'SOFTWARE'),
    ('Proliferación descontrolada de VM (VM sprawl)', 'SOFTWARE'),
    ('Plantilla base (golden image) comprometida', 'SOFTWARE'),
    ('Agotamiento de recursos compartidos (noisy neighbor)', 'SOFTWARE'),
    ('Compromiso de la consola de administración de virtualización', 'SOFTWARE'),
    ('Avería de origen Físico o Lógico', 'EQUIPAMIENTO')
),
validas AS (
    SELECT DISTINCT r.amenaza, r.tipo_activo_txt
    FROM relaciones r
    JOIN pg_type t
      ON t.typname = 'tipo_magerit'
    JOIN pg_namespace ns
      ON ns.oid = t.typnamespace
     AND ns.nspname = 'public'
    JOIN pg_enum e
      ON e.enumtypid = t.oid
     AND e.enumlabel = r.tipo_activo_txt
)
INSERT INTO public.amenaza_tipo_activo (amenaza_id, tipo_activo)
SELECT
    ca.id,
    v.tipo_activo_txt::public.tipo_magerit
FROM validas v
JOIN public.catalogo_amenazas ca
  ON lower(trim(ca.amenaza)) = lower(trim(v.amenaza))
WHERE NOT EXISTS (
    SELECT 1
    FROM public.amenaza_tipo_activo ata
    WHERE ata.amenaza_id = ca.id
      AND ata.tipo_activo = v.tipo_activo_txt::public.tipo_magerit
);

COMMIT;

-- ============================================================
-- 4) COMPROBACIONES
-- ============================================================
SELECT COUNT(*) AS total_amenazas
FROM public.catalogo_amenazas;

SELECT COUNT(*) AS total_relaciones_tipo_activo
FROM public.amenaza_tipo_activo;

SELECT
    ca.amenaza,
    ca.probabilidad,
    ca.subcategoria,
    ca.degradacion_d,
    ca.degradacion_i,
    ca.degradacion_c,
    ca.degradacion_a,
    ca.degradacion_t,
    string_agg(ata.tipo_activo::text, '; ' ORDER BY ata.tipo_activo::text) AS tipos_activo
FROM public.catalogo_amenazas ca
LEFT JOIN public.amenaza_tipo_activo ata
  ON ata.amenaza_id = ca.id
WHERE ca.amenaza IN (
    'Denegación de servicio (DoS/DDoS)',
    'Ransomware',
    'Configuración insegura',
    'Acceso no autorizado a la información'
)
GROUP BY
    ca.id, ca.amenaza, ca.probabilidad, ca.subcategoria,
    ca.degradacion_d, ca.degradacion_i, ca.degradacion_c,
    ca.degradacion_a, ca.degradacion_t
ORDER BY ca.amenaza;

-- Amenazas de IA: deben existir en catálogo, pero sin relación de tipo de activo
SELECT COUNT(*) AS amenazas_ia_sin_relacion
FROM public.catalogo_amenazas ca
JOIN public.categorias_amenaza c
  ON c.id = ca.categoria_id
LEFT JOIN public.amenaza_tipo_activo ata
  ON ata.amenaza_id = ca.id
WHERE c.nombre = 'Amenazas de Inteligencia Artificial'
  AND ata.amenaza_id IS NULL;
