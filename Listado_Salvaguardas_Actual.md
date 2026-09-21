# Catálogo de salvaguardas — estado actual del proyecto

**Fecha:** 09/09/2026
**Fuente:** Anexo II del Esquema Nacional de Seguridad (RD 311/2022, texto consolidado 06/11/2024) — BOE-A-2022-7191

## Resumen

| | |
|---|---|
| Salvaguardas en el catálogo | 74 |
| Con eficacia directa activa (tipos PR/DR/EL/IM/CR/RC) | 68 |
| Con eficacia provisional (tipos MN/DC/AW — "consolidan el efecto de las demás") | 6 |
| Vínculos a categorías de amenaza | 87 |
| Medidas descartadas por no existir en el BOE oficial | `op.acc.7`, `op.acc.8` |

## Qué es dato oficial y qué es criterio del proyecto

- **Código y nombre**: 100% oficial, verificado línea a línea contra el BOE.
- **Tipo Magerit, eficacia (ei/ep) y categoría de amenaza protegida**: criterio del proyecto — Magerit no fija estos valores para ninguna salvaguarda (ni del ENS ni de ningún otro catálogo), los deja explícitamente a estimación del analista. La tabla de eficacia por tipo está calibrada con datos reales del CIS Community Defense Model v2.0, no inventada a ciegas.
- **Multiplicador de madurez (L0-L5)**: se aplica además, por cada asignación concreta a un activo — no está reflejado en esta tabla, que muestra la eficacia "de catálogo" (equivalente a madurez L5/óptima).

## Listado completo

Total medidas: 74

| Código | Nombre | Aplica | Tipo | ei% | ep% | Categoría(s) de amenaza |
|---|---|---|---|---|---|---|
| org.1 | Politica de seguridad | BMA | PR | 0 | 60 | Errores humanos / operacionales |
| org.2 | Normativa de seguridad | BMA | PR | 0 | 60 | Errores humanos / operacionales |
| org.3 | Procedimientos de seguridad | BMA | PR | 0 | 60 | Errores humanos / operacionales |
| org.4 | Proceso de autorizacion | BMA | PR | 0 | 60 | Robo y abuso de credenciales |
| op.pl.1 | Analisis de riesgos | BMA | PR | 0 | 60 | Errores humanos / operacionales |
| op.pl.2 | Arquitectura de seguridad | BMA | EL | 0 | 80 | Explotacion de vulnerabilidades / Malware |
| op.pl.3 | Adquisicion de nuevos componentes | BMA | PR | 0 | 60 | Cadena de suministro / Terceros |
| op.pl.4 | Dimensionamiento/gestion de la capacidad | BMA | IM | 60 | 0 | Denegacion de Servicio |
| op.pl.5 | Componentes certificados | MA | PR | 0 | 60 | Cadena de suministro / Terceros |
| op.acc.1 | Identificacion | MA | PR | 0 | 60 | Robo y abuso de credenciales |
| op.acc.2 | Requisitos de acceso | BMA | PR | 0 | 60 | Robo y abuso de credenciales |
| op.acc.3 | Segregacion de funciones y tareas | MA | PR | 0 | 60 | Amenazas internas (insider) |
| op.acc.4 | Gestion de derechos de acceso | BMA | PR | 0 | 60 | Robo y abuso de credenciales |
| op.acc.5 | Mecanismo de autenticacion (usuarios externos) | BMA | EL | 0 | 80 | Robo y abuso de credenciales, Phishing / Ingenieria social |
| op.acc.6 | Mecanismo de autenticacion (usuarios internos) | BMA | EL | 0 | 80 | Robo y abuso de credenciales, Phishing / Ingenieria social |
| op.exp.1 | Inventario de activos | BMA | PR | 0 | 60 | Errores humanos / operacionales |
| op.exp.2 | Configuracion de seguridad | BMA | EL | 0 | 80 | Explotacion de vulnerabilidades / Malware |
| op.exp.3 | Gestion de la configuracion de seguridad | BMA | EL | 0 | 80 | Explotacion de vulnerabilidades / Malware |
| op.exp.4 | Mantenimiento y actualizaciones de seguridad | BMA | EL | 0 | 80 | Explotacion de vulnerabilidades / Malware |
| op.exp.5 | Gestion de cambios | MA | PR | 0 | 60 | Errores humanos / operacionales |
| op.exp.6 | Proteccion frente a codigo danino | BMA | IM | 60 | 0 | Explotacion de vulnerabilidades / Malware, Ransomware / Extorsion |
| op.exp.7 | Gestion de incidentes | BMA | CR | 50 | 0 | Ransomware / Extorsion, Explotacion de vulnerabilidades / Malware |
| op.exp.8 | Registro de la actividad | BMA | DC (aparcado) | 0 | 0 | Trazabilidad y logs |
| op.exp.9 | Registro de la gestion de incidentes | BMA | PR | 0 | 60 | Trazabilidad y logs |
| op.exp.10 | Proteccion de claves criptograficas | BMA | EL | 0 | 80 | Criptografico |
| op.ext.1 | Contratacion y acuerdos de nivel de servicio | MA | PR | 0 | 60 | Cadena de suministro / Terceros |
| op.ext.2 | Gestion diaria | MA | PR | 0 | 60 | Cadena de suministro / Terceros |
| op.ext.3 | Proteccion de la cadena de suministro | A | PR | 0 | 60 | Cadena de suministro / Terceros |
| op.ext.4 | Interconexion de sistemas | MA | PR | 0 | 60 | Comunicaciones / Red |
| op.nub.1 | Proteccion de servicios en la nube | BMA | PR | 0 | 60 | Cloud |
| op.cont.1 | Analisis de impacto | MA | PR | 0 | 60 | Denegacion de Servicio, Desastres |
| op.cont.2 | Plan de continuidad | A | RC | 40 | 0 | Desastres, Denegacion de Servicio |
| op.cont.3 | Pruebas periodicas | A | RC | 40 | 0 | Desastres |
| op.cont.4 | Medios alternativos | A | RC | 40 | 0 | Desastres, Denegacion de Servicio |
| op.mon.1 | Deteccion de intrusion | BMA | DC (aparcado) | 0 | 0 | Explotacion de vulnerabilidades / Malware |
| op.mon.2 | Sistema de metricas | BMA | MN (aparcado) | 0 | 0 | Errores humanos / operacionales |
| op.mon.3 | Vigilancia | BMA | MN (aparcado) | 0 | 0 | Amenazas internas (insider) |
| mp.if.1 | Areas separadas y con control de acceso | BMA | PR | 0 | 60 | Amenazas fisicas, Robo o perdida |
| mp.if.2 | Identificacion de las personas | BMA | PR | 0 | 60 | Amenazas fisicas |
| mp.if.3 | Acondicionamiento de los locales | BMA | PR | 0 | 60 | Amenazas fisicas |
| mp.if.4 | Energia electrica | BMA | IM | 60 | 0 | Desastres |
| mp.if.5 | Proteccion frente a incendios | BMA | IM | 60 | 0 | Desastres |
| mp.if.6 | Proteccion frente a inundaciones | MA | IM | 60 | 0 | Desastres |
| mp.if.7 | Registro de entrada y salida de equipamiento | BMA | PR | 0 | 60 | Robo o perdida |
| mp.per.1 | Caracterizacion del puesto de trabajo | MA | PR | 0 | 60 | Amenazas internas (insider) |
| mp.per.2 | Deberes y obligaciones | BMA | PR | 0 | 60 | Amenazas internas (insider), Errores humanos / operacionales |
| mp.per.3 | Concienciacion | BMA | AW (aparcado) | 0 | 0 | Phishing / Ingenieria social, Errores humanos / operacionales |
| mp.per.4 | Formacion | BMA | AW (aparcado) | 0 | 0 | Phishing / Ingenieria social, Errores humanos / operacionales |
| mp.eq.1 | Puesto de trabajo despejado | BMA | PR | 0 | 60 | Robo o perdida |
| mp.eq.2 | Bloqueo de puesto de trabajo | MA | PR | 0 | 60 | Robo y abuso de credenciales |
| mp.eq.3 | Proteccion de dispositivos portatiles | BMA | PR | 0 | 60 | Robo o perdida |
| mp.eq.4 | Otros dispositivos conectados a la red | BMA | PR | 0 | 60 | Comunicaciones / Red |
| mp.com.1 | Perimetro seguro | BMA | EL | 0 | 80 | Comunicaciones / Red, Explotacion de vulnerabilidades / Malware |
| mp.com.2 | Proteccion de la confidencialidad | BMA | EL | 0 | 80 | Exfiltracion, fuga de informacion y espionaje |
| mp.com.3 | Proteccion de la integridad y de la autenticidad | BMA | EL | 0 | 80 | Manipulacion e integridad de datos |
| mp.com.4 | Separacion de flujos de informacion en la red | MA | IM | 60 | 0 | Comunicaciones / Red |
| mp.si.1 | Marcado de soportes | MA | PR | 0 | 60 | Robo o perdida |
| mp.si.2 | Criptografia | MA | EL | 0 | 80 | Robo o perdida, Exfiltracion, fuga de informacion y espionaje |
| mp.si.3 | Custodia | BMA | PR | 0 | 60 | Robo o perdida |
| mp.si.4 | Transporte | BMA | PR | 0 | 60 | Robo o perdida |
| mp.si.5 | Borrado y destruccion | BMA | EL | 0 | 80 | Exfiltracion, fuga de informacion y espionaje |
| mp.sw.1 | Desarrollo de aplicaciones | MA | PR | 0 | 60 | Explotación de vulnerabilidades / Malware, Ataques a aplicaciones web |
| mp.sw.2 | Aceptación y puesta en servicio | BMA | PR | 0 | 60 | Explotación de vulnerabilidades / Malware |
| mp.info.1 | Datos de carácter personal | BMA | PR | 0 | 60 | Exfiltración, fuga de información y espionaje |
| mp.info.2 | Calificación de la información | A | PR | 0 | 60 | Exfiltración, fuga de información y espionaje |
| mp.info.3 | Cifrado de la información | BMA | EL | 0 | 80 | Exfiltración, fuga de información y espionaje |
| mp.info.4 | Firma electrónica | MA | EL | 0 | 80 | Manipulación e integridad de datos |
| mp.info.5 | Sellos de tiempo | BMA | EL | 0 | 80 | Manipulación e integridad de datos |
| mp.info.6 | Limpieza de documentos publicados | MA | PR | 0 | 60 | Exfiltración, fuga de información y espionaje |
| mp.info.9 | Copias de seguridad (backup) | BMA | RC | 40 | 0 | Ransomware / Extorsión, Desastres |
| mp.s.1 | Protección del correo electrónico | BMA | EL | 0 | 80 | Phishing / Ingeniería social |
| mp.s.2 | Protección de servicios y aplicaciones web | BMA | EL | 0 | 80 | Ataques a aplicaciones web |
| mp.s.3 | Protección de la navegación web | BMA | EL | 0 | 80 | Explotación de vulnerabilidades / Malware |
| mp.s.4 | Protección frente a la denegación de servicio | MA | IM | 60 | 0 | Denegación de Servicio |
