# pitwall-mcp: qué hace, cómo lo hace y para qué sirve

Resumen del proyecto a fecha del 19 de septiembre de 2026, escrito para alguien
que llega de nuevas. El [README](../README.md) es la guía de uso; esto es la
explicación.

> Proyecto **no afiliado a BMW AG**. "BMW" y "CarData" son marcas de sus
> respectivos propietarios.

---

## El problema

BMW ofrece a los clientes una API llamada **CarData** con los datos telemáticos
del coche. Es útil y está infrautilizada, pero tiene tres asperezas que la hacen
incómoda de consultar a mano:

1. **La cuota es minúscula y compartida**: 50 peticiones cada 24 horas *por
   cuenta*, no por aplicación. Pasarse devuelve un error hasta el día siguiente.
   Cualquier consulta descuidada la agota.
2. **Los datos no son una foto coherente.** Una sola respuesta mezcla valores
   medidos hace un minuto con otros de hace cuatro días, cada uno con su propio
   sello de tiempo. Leer un número sin mirar su sello lleva a conclusiones
   falsas.
3. **Lo que falta, falta de tres maneras distintas**: el campo no viene, viene
   vacío, o viene con un "sin medida" explícito. No son lo mismo, y tratarlos
   igual es mentir.

Encima, la app oficial enseña el estado de hoy y nada más: no guarda serie, así
que no puede decir *cuándo* toca algo ni *qué ha cambiado* desde la última vez.

## Qué hace

Un **servidor MCP local de solo lectura**: le da a un asistente de IA acceso a
los datos del coche, en lenguaje natural, sin que pueda tocar el vehículo y sin
agotar la cuota.

Once herramientas, agrupadas por lo que responden:

- **Mantenimiento**: kilometraje, avisos CBS partida por partida, inspección
  legal, avisos Check Control y presiones contra su objetivo.
- **Combustible**: depósito, autonomía, repostajes detectados en el histórico y
  consumo real desde el último, siempre con su margen de error.
- **Averías**: la memoria de la centralita agrupada, y qué códigos aparecen o
  desaparecen entre lecturas.
- **Infraestructura**: cuota disponible, búsqueda en el catálogo de
  descriptores, y datos básicos del vehículo.

## Cómo lo hace

Cuatro decisiones de diseño sostienen todo lo demás.

**1. Ninguna petición sin pasar por caché.** Todas las llamadas atraviesan un
adaptador que consulta primero una caché en SQLite, con un TTL por endpoint (12 h
los datos telemáticos, 30 días los del vehículo). Un contador local lleva la
cuenta del día y corta en 20 peticiones, dejando margen sobre las 50 de BMW. El
uso normal son **2 peticiones diarias**.

**2. Un solo contenedor gordo.** CarData obliga a declarar por adelantado qué
descriptores quieres. En vez de varios, hay uno con los 42 confirmados, así que
una sola petición trae kilometraje, mantenimiento, presiones, batería, depósito y
memoria de averías. Crear ese contenedor es lo único que escribe en la cuenta, y
vive **fuera del servidor**, en un script que se lanza a mano.

**3. Todo se guarda, para poder razonar sobre series.** Cada lectura va a una
tabla histórica con el sello de BMW y el nuestro. Eso es lo que permite decir
"este código de avería es nuevo" o "esta rueda se está separando de su pareja",
que es justo lo que una foto no puede decir. Al comparar, se colapsan las
repeticiones: un valor reenviado sin cambiar no cuenta como observación nueva.

**4. El catálogo es la única fuente de verdad.** Un descriptor que no esté en el
catálogo oficial no existe, y no se deduce por analogía ni se copia de terceros.

Encima de eso, una regla que atraviesa el código entero: **cuando un dato no
está, la herramienta dice por qué no está**. No devuelve cero, ni un valor
plausible, ni un `null` a secas. Distingue los tres estados de ausencia, declara
de dónde viene cada dato y con qué antigüedad, y cuando dos cifras de BMW no
cuadran entre sí, **da las dos y dice que no cuadran** en vez de elegir una.

**Stack**: Python 3.12 asíncrono, SDK oficial de MCP sobre stdio, SQLite de la
biblioteca estándar, y la librería `bmw-cardata` pinneada a una versión alfa
exacta y envuelta en un adaptador propio, para que un cambio suyo se toque en un
solo fichero. Más de 400 tests, **ninguno de los cuales hace una llamada real**:
todos contra respuestas grabadas. Una suite que gaste cuota sería un fallo.

## Para qué sirve

Para preguntarle al asistente cosas que antes había que deducir a mano:

- *"¿Cuándo me toca cambiar las pastillas?"* → no "1.600 km", sino unas 2,8
  semanas al ritmo real de uso, con fecha, y avisando de que BMW ya la marca como
  pendiente.
- *"¿Me pierde aire una rueda?"* → compara cada rueda con su pareja de eje en la
  misma lectura, porque el objetivo de presión de BMW sube con el calor y
  compararlo con otro momento confundiría el tiempo que hace con una fuga.
- *"¿Ha cambiado algo en el coche desde la semana pasada?"* → el 18 de septiembre
  esa pregunta devolvió once códigos de avería nuevos y otros once desaparecidos,
  con una centralita entera apareciendo. Eso no se ve en ningún sitio si no
  guardas la lectura anterior.

## Lo que deliberadamente no hace

- **No escribe nada en el coche.** CarData es de lectura, y ninguna herramienta
  insinúa lo contrario.
- **No traduce los códigos de avería.** Su significado no está en el catálogo;
  inventarlo sería peor que no darlo. Para eso hace falta una herramienta de
  diagnóstico por OBD.
- **No registra la posición.** Los descriptores de localización existen y están
  fuera a propósito.
- **No promete lo que el coche no manda.** De los 42 descriptores pedidos, 13
  llegan siempre vacíos en este vehículo, y la documentación lo dice en lugar de
  presumir de funciones que no funcionarían.

## Límites honestos

- **Verificado en un solo coche**: un BMW X1 sDrive18i (U11) de gasolina. El
  catálogo es común a toda la gama, pero qué emite cada vehículo solo se sabe
  leyéndolo.
- **La API no sirve para series de todo.** Un grupo de descriptores —el voltaje
  de la batería entre ellos— se refresca muy de tarde en tarde, y en cinco
  lecturas todas sus muestras han sido con el motor en marcha. Para tener serie
  de la batería en reposo haría falta el streaming MQTT, que está diseñado pero
  no implementado.
- **La librería sobre la que se apoya está en alfa**, y por eso va pinneada.
- **Requiere tus propias credenciales** de CarData y ser usuario primario del
  vehículo. No hay servicio central: todo corre en tu máquina.
