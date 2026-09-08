"""Errors translated into Spanish instructions.

Every error tells the user what to DO, not just what failed. The wording is the
user-facing part of the project, so it is in Spanish while the code around it
stays in English (CLAUDE.md, rule 10).
"""

from __future__ import annotations

from bmw_cardata.exceptions import (
    CarDataAuthError,
    CarDataError,
    CarDataHTTPError,
    ExpiredTokenError,
)

from ..storage.quota import QUOTA_ERROR_ID, QuotaExceededError

PORTAL_URL = "https://bmw-cardata.bmwgroup.com"

SCOPE_API_READ = "cardata:api:read"
SCOPE_STREAMING_READ = "cardata:streaming:read"


class PitwallError(RuntimeError):
    """Base class for every error a tool may show to the user."""

    def __init__(self, message: str, *, cause: Exception | None = None) -> None:
        """Build the error from its Spanish message and the original exception."""
        super().__init__(message)
        self.message = message
        self.cause = cause


class MissingCredentialsError(PitwallError):
    """No client id or no token file: nothing can be requested yet."""


class MissingConfigError(PitwallError):
    """A required setting (VIN, container id) is not configured."""


class MissingScopeError(PitwallError):
    """The token lacks one of the CarData scopes."""


class RefreshTokenExpiredError(PitwallError):
    """The 14-day refresh token is gone; the browser flow must be redone."""


class QuotaExhaustedError(PitwallError):
    """No further request may be sent today."""


class EmptyContainerError(PitwallError):
    """The container returned no mapped fields."""


class VinNotPrimaryError(PitwallError):
    """CarData requires the account to be the vehicle's PRIMARY user."""


class CarDataUnavailableError(PitwallError):
    """BMW answered with a server-side or otherwise unexpected error."""


class TokenLockTimeoutError(PitwallError):
    """Another process held the token lock for too long."""


def token_lock_timeout(lock_path, timeout) -> TokenLockTimeoutError:  # noqa: ANN001
    """Explain a token-lock timeout without suggesting the user delete anything."""
    seconds = int(timeout.total_seconds())
    return TokenLockTimeoutError(
        f"Otro proceso lleva mas de {seconds} s renovando los tokens y no ha soltado el "
        f"cerrojo ({lock_path}). Normalmente es el daemon de streaming de la Fase 2: "
        f"BMW rota el refresh token en cada renovacion, asi que solo un proceso puede "
        f"renovarlo a la vez. Vuelve a intentarlo en unos segundos. Si se repite, "
        f"comprueba que no haya quedado un proceso colgado; el fichero de cerrojo se "
        f"libera solo cuando el proceso que lo tiene termina."
    )


def missing_credentials(missing: list[str]) -> MissingCredentialsError:
    """Explain which credentials are missing and how to obtain them."""
    detalle = ", ".join(missing) if missing else "credenciales"
    return MissingCredentialsError(
        f"Faltan credenciales de BMW CarData: {detalle}. "
        f"Rellena PITWALL_CLIENT_ID en el fichero .env con el client id de tu "
        f"aplicacion CarData ({PORTAL_URL}) y despues ejecuta "
        f"'python scripts/login.py' para completar el device flow en el navegador. "
        f"Esta herramienta no puede leer nada del vehiculo hasta entonces."
    )


def missing_vin() -> MissingConfigError:
    """Explain that no VIN is configured."""
    return MissingConfigError(
        "No hay VIN configurado. Anade PITWALL_VIN al fichero .env, o pasa el VIN "
        "explicitamente. Puedes ver los VIN de tu cuenta con la herramienta "
        "list_vehicles una vez tengas credenciales."
    )


def bad_catalogue_download(url: str, path) -> MissingConfigError:  # noqa: ANN001 - Path
    """Explain that the download did not bring back a catalogue, and wrote nothing."""
    return MissingConfigError(
        f"Lo descargado de {url} no parece el catalogo telematico, asi que NO se ha "
        f"escrito nada en {path}. Suele ser un portal cautivo, un proxy corporativo o "
        f"una red que exige inicio de sesion: comprueba la conexion y vuelve a "
        f"intentarlo. Si ya tenias un catalogo valido ahi, sigue intacto."
    )


def missing_catalogue(path) -> MissingConfigError:  # noqa: ANN001 - Path
    """Explain that the telematic catalogue has not been downloaded yet."""
    return MissingConfigError(
        f"No encuentro el catalogo telematico en {path}. No viene dentro del "
        f"repositorio: descargalo una vez con 'python scripts/refresh_catalogue.py'. "
        f"La descarga es de GitHub, no de BMW, asi que no gasta ninguna peticion de "
        f"tu cuota. Si lo tienes en otro sitio, apunta PITWALL_CATALOGUE_PATH a el."
    )


def missing_container() -> MissingConfigError:
    """Explain that no container id is configured."""
    return MissingConfigError(
        "No hay contenedor configurado. /telematicData exige un containerId. "
        "Ejecuta 'python scripts/bootstrap_containers.py' (primero con --dry-run "
        "para ver que enviaria) y copia el id resultante a PITWALL_CONTAINER_ID "
        "en el .env. El servidor MCP nunca crea ni borra contenedores."
    )


def empty_container(container_id: str) -> EmptyContainerError:
    """Explain an empty telematic response."""
    return EmptyContainerError(
        f"El contenedor '{container_id}' no ha devuelto ningun campo. "
        f"Revisa que el contenedor exista y tenga descriptores con "
        f"'python scripts/bootstrap_containers.py --list'. Si lo que falla es el "
        f"streaming y no la REST, la seleccion de datos (Data Selection) solo se "
        f"configura desde el portal: {PORTAL_URL}."
    )


def quota_exhausted(error: QuotaExceededError) -> QuotaExhaustedError:
    """Wrap the local quota guard as a user-facing error."""
    return QuotaExhaustedError(error.status.spanish_message(), cause=error)


def translate_http_error(error: CarDataHTTPError, *, vin: str | None = None) -> PitwallError:
    """Map an HTTP error from the API onto an actionable Spanish message."""
    message = (error.message or "").lower()
    note = (error.note or "").lower()
    haystack = f"{message} {note}"

    if error.error_id == QUOTA_ERROR_ID or error.status == 429:
        return QuotaExhaustedError(
            f"BMW ha rechazado la peticion con {error.status} {error.error_id or ''} "
            f"(cuota de la cuenta agotada). El limite es de 50 peticiones cada 24 h y "
            f"lo cuenta BMW, no nosotros: incluye cualquier otra aplicacion que use la "
            f"misma cuenta. No se puede leer nada nuevo hasta que la ventana se reinicie; "
            f"mientras tanto las herramientas solo sirven cache e historico local. "
            f"Mensaje de BMW: {error.message}",
            cause=error,
        )

    if "scope" in haystack or "insufficient" in haystack:
        return MissingScopeError(
            f"El token no tiene los permisos necesarios. Para leer la REST hace falta el "
            f"scope '{SCOPE_API_READ}'; para el streaming, '{SCOPE_STREAMING_READ}'. "
            f"Autorizalos en tu aplicacion CarData del portal ({PORTAL_URL}) y vuelve a "
            f"ejecutar 'python scripts/login.py' para obtener un token nuevo. "
            f"Mensaje de BMW: {error.message}",
            cause=error,
        )

    if error.status == 401:
        return RefreshTokenExpiredError(
            f"BMW ha rechazado el token (401). Lo mas probable es que el access token haya "
            f"caducado y el refresh token ya no sirva: dura 14 dias. Ejecuta "
            f"'python scripts/login.py' y completa el device flow en el navegador. "
            f"Mensaje de BMW: {error.message}",
            cause=error,
        )

    if error.status in (403, 404) and ("primary" in haystack or "mapping" in haystack):
        return VinNotPrimaryError(
            f"CarData exige que la cuenta sea usuario PRIMARY del vehiculo"
            f"{f' {vin}' if vin else ''}. Si el VIN aparece como SECONDARY en "
            f"list_vehicles, no hay forma de leerlo por API: hay que cambiar la "
            f"titularidad en la app My BMW. Mensaje de BMW: {error.message}",
            cause=error,
        )

    if error.status == 403:
        return CarDataUnavailableError(
            f"BMW ha denegado el acceso (403{f' {error.error_id}' if error.error_id else ''}). "
            f"Revisa que la aplicacion CarData del portal siga activa y que el VIN este "
            f"mapeado a esta cuenta. Mensaje de BMW: {error.message}",
            cause=error,
        )

    if error.status == 404:
        return CarDataUnavailableError(
            f"BMW no encuentra el recurso (404). Si es un contenedor, puede que se haya "
            f"borrado: vuelve a ejecutar 'python scripts/bootstrap_containers.py'. Si es un "
            f"VIN, comprueba list_vehicles. Mensaje de BMW: {error.message}",
            cause=error,
        )

    if error.status >= 500:
        return CarDataUnavailableError(
            f"BMW ha respondido con un error de servidor ({error.status}). No es un fallo de "
            f"configuracion: vuelve a intentarlo mas tarde. Ten en cuenta que esta peticion "
            f"ya ha consumido cuota. Mensaje de BMW: {error.message}",
            cause=error,
        )

    return CarDataUnavailableError(
        f"Error inesperado de la API de BMW ({error.status}"
        f"{f', {error.error_id}' if error.error_id else ''}): {error.message}",
        cause=error,
    )


def translate_auth_error(error: CarDataAuthError) -> PitwallError:
    """Map an OAuth error onto an actionable Spanish message."""
    if isinstance(error, ExpiredTokenError) or error.error in (
        "expired_token",
        "invalid_grant",
    ):
        return RefreshTokenExpiredError(
            f"El refresh token ha caducado (dura 14 dias). Ejecuta "
            f"'python scripts/login.py' y completa el device flow en el navegador para "
            f"obtener uno nuevo. Error de BMW: {error.error}",
            cause=error,
        )
    if error.error == "access_denied":
        return MissingCredentialsError(
            "La autorizacion fue denegada en el navegador. Vuelve a ejecutar "
            "'python scripts/login.py' y acepta la peticion.",
            cause=error,
        )
    return MissingCredentialsError(
        f"Fallo de autenticacion con BMW: {error}. Ejecuta 'python scripts/login.py' "
        f"para rehacer el login.",
        cause=error,
    )


def translate(error: Exception, *, vin: str | None = None) -> PitwallError:
    """Translate any known exception into a `PitwallError`."""
    if isinstance(error, PitwallError):
        return error
    if isinstance(error, QuotaExceededError):
        return quota_exhausted(error)
    if isinstance(error, CarDataHTTPError):
        return translate_http_error(error, vin=vin)
    if isinstance(error, CarDataAuthError):
        return translate_auth_error(error)
    if isinstance(error, CarDataError):
        return CarDataUnavailableError(
            f"Error de la libreria bmw-cardata: {error}", cause=error
        )
    return CarDataUnavailableError(f"Error inesperado: {error}", cause=error)
