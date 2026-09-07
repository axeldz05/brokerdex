# Credenciales locales y rotación

Los secretos de desarrollo no deben vivir dentro del repositorio. Los archivos
`brokerdex/.my_pgpass` y `brokerdex/.pg_service.conf` fueron retirados del
índice, pero pueden seguir existiendo en clones e historial anteriores. No
leerlos, copiarlos ni usarlos como plantilla: las credenciales que hayan
contenido deben considerarse expuestas y rotarse.

## Setup local seguro

1. Crear un directorio privado fuera del repositorio:

   ```bash
   install -d -m 700 "$HOME/.config/brokerdex"
   ```

2. Crear un archivo `pgpass` nuevo en ese directorio, completar únicamente con
   credenciales locales vigentes y limitar sus permisos:

   ```bash
   touch "$HOME/.config/brokerdex/pgpass"
   chmod 600 "$HOME/.config/brokerdex/pgpass"
   export PGPASSFILE="$HOME/.config/brokerdex/pgpass"
   ```

3. Si se usan servicios PostgreSQL, crear también
   `$HOME/.config/brokerdex/pg_service.conf`, sin contraseñas, protegerlo y
   declararlo:

   ```bash
   touch "$HOME/.config/brokerdex/pg_service.conf"
   chmod 600 "$HOME/.config/brokerdex/pg_service.conf"
   export PGSERVICEFILE="$HOME/.config/brokerdex/pg_service.conf"
   ```

4. Guardar variables sensibles en un `.env` local solo cuando la configuración
   de la aplicación las consuma. Si existe `.env.example`, copiarlo a `.env` y
   completar los valores localmente; nunca agregar secretos al ejemplo.
5. Persistir las variables `PGPASSFILE` y `PGSERVICEFILE` en un mecanismo local
   privado, no en scripts versionados. Verificar antes de cada commit que ningún
   secreto aparezca en `git status`.

## Rotación humana obligatoria

1. El responsable de PostgreSQL debe identificar, por un canal autorizado y sin
   pegar valores en tickets o logs, todos los roles y entornos afectados.
2. Generar credenciales nuevas en PostgreSQL o en el gestor de secretos y
   distribuirlas únicamente a los consumidores autorizados.
3. Actualizar servicios y archivos locales externos al repositorio; comprobar
   conectividad con las credenciales nuevas.
4. Revocar las credenciales anteriores, cerrar sesiones antiguas cuando
   corresponda y revisar registros de acceso por actividad inesperada.
5. Registrar quién completó la rotación, la fecha y los entornos cubiertos, sin
   registrar valores secretos.

La eliminación del archivo en el commit no reemplaza la rotación.

## Purga coordinada del historial

La purga reescribe todos los SHA afectados y requiere aprobación de los
responsables del repositorio:

1. Congelar merges y pushes, avisar a todos los colaboradores y conservar un
   respaldo controlado.
2. Reescribir todas las ramas y etiquetas con una herramienta como
   `git filter-repo`, eliminando exactamente:
   `brokerdex/.my_pgpass` y `brokerdex/.pg_service.conf`.
3. Revisar el resultado sin mostrar el contenido eliminado y forzar la
   actualización coordinada de ramas y etiquetas remotas, respetando las
   protecciones administradas.
4. Pedir a los colaboradores que eliminen clones antiguos y vuelvan a clonar;
   invalidar artefactos, cachés y respaldos que hayan retenido los archivos.
5. Consultar al proveedor del hosting sobre copias, forks o cachés fuera del
   alcance de la reescritura y documentar las limitaciones restantes.

La purga no garantiza que desaparezcan copias externas y tampoco reemplaza la
rotación y revocación de las credenciales.
