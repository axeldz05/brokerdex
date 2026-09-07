# Webhook genérico de depósitos

Brokerdex expone un borde mínimo, no específico de proveedor, para acreditar
depósitos completados. El endpoint no usa sesión ni CSRF; HMAC-SHA256 sobre el
body crudo es su autenticación.

## Endpoint

```text
POST /webhooks/deposits/
Content-Type: application/json
X-Webhook-Signature: <hex-hmac-sha256>
```

También se acepta el prefijo:

```text
X-Webhook-Signature: sha256=<hex-hmac-sha256>
```

Otros métodos responden 405. El endpoint queda deshabilitado de forma segura si
`DEPOSIT_WEBHOOK_SECRET` está vacío: toda solicitud responde 401.

## Firma

El proveedor y Brokerdex deben compartir `DEPOSIT_WEBHOOK_SECRET` por un canal
seguro. La firma es:

```text
hex(HMAC-SHA256(secret_utf8, exact_request_body_bytes))
```

Firmar exactamente los bytes enviados. Cambiar espacios, orden de claves,
encoding o salto final después de firmar invalida la solicitud. La comparación
en el servidor es constante mediante `hmac.compare_digest`.

Ejemplo reproducible sin escribir el secreto en el comando:

```bash
body='{"status":"COMPLETED","provider_reference":"provider-event-001","target_identifier":"0000000000000000000001","identifier_type":"CVU","amount_cents":2500}'
signature="$(
  BODY="$body" uv run python -c 'import hashlib,hmac,os; print(hmac.new(os.environ["DEPOSIT_WEBHOOK_SECRET"].encode(), os.environ["BODY"].encode(), hashlib.sha256).hexdigest())'
)"
curl --fail-with-body \
  --request POST \
  --header "Content-Type: application/json" \
  --header "X-Webhook-Signature: sha256=${signature}" \
  --data-binary "$body" \
  "https://brokerdex.example.com/webhooks/deposits/"
unset body signature
```

Definir `DEPOSIT_WEBHOOK_SECRET` en el entorno privado de la terminal antes de
ejecutar el ejemplo. No pegar el valor ni la firma en shell history, tickets o
logs compartidos. Sustituir host, referencia e identificador por valores del
entorno de prueba autorizado.

## Payload

```json
{
  "status": "COMPLETED",
  "provider_reference": "provider-event-001",
  "target_identifier": "0000000000000000000001",
  "identifier_type": "CVU",
  "amount_cents": 2500
}
```

Contrato estricto:

- `status` debe ser exactamente `COMPLETED`.
- `provider_reference` debe ser string no vacío de hasta 255 caracteres.
- `target_identifier` debe ser string no vacío de hasta 100 caracteres.
- `identifier_type` debe ser `CVU` o `ALIAS`.
- `amount_cents` debe ser un entero positivo. Strings, booleanos, cero y
  negativos son inválidos.

El monto está en centavos, no en unidades decimales. El servicio busca una
cuenta existente por el identificador exacto; el flujo normal genera CVU de 22
dígitos y alias del tipo `brokerdex.<token>.<token>`.

## Idempotencia y atomicidad

`provider_reference` es la clave persistente de idempotencia para todos los
depósitos:

1. el servicio abre una transacción;
2. busca y bloquea una confirmación existente;
3. bloquea la cuenta destino;
4. crea un único `Transfer` completado;
5. acredita `balance_cents`;
6. una restricción condicional única resuelve carreras concurrentes.

Repetir la misma referencia devuelve 200 y no vuelve a acreditar. La
referencia debe ser globalmente única, estable e inmutable para la integración,
porque el modelo actual no agrega un namespace de proveedor.

El comportamiento actual deduplica por referencia sin comparar nuevamente
monto, destino o tipo. El proveedor no debe reutilizar una referencia con datos
contradictorios; esa contradicción no genera hoy una alerta automática y está
documentada como deuda en [AGENTS.md](../AGENTS.md).

## Respuestas

Éxito inicial o reintento idempotente:

```text
200 {"status":"ok"}
```

Errores estables:

- 400 `{"status":"error","code":"invalid_payload"}`.
- 401 `{"status":"error","code":"invalid_signature"}`.
- 404 `{"status":"error","code":"account_not_found"}`.
- 405 `{"status":"error","code":"method_not_allowed"}`.
- 500 `{"status":"error","code":"internal_error"}`.

Las respuestas incluyen `X-Request-ID`. Conservarlo para correlación sin
registrar el body ni la firma.

## Checklist de integración

- Usar HTTPS y terminar TLS solo en infraestructura confiable.
- Inyectar un secreto aleatorio distinto por entorno.
- Confirmar que proxy/WAF preserve el body exacto y el header de firma.
- Definir referencias únicas y reintentar el mismo evento con el mismo body.
- No enviar estados distintos de `COMPLETED`.
- Probar CVU/alias desconocido y firma inválida sin acreditar saldo.
- Probar dos entregas simultáneas y verificar un único `Transfer`.
- Rotar el secreto ante exposición; coordinar una ventana de cambio porque solo
  existe un secreto activo.
- Añadir timestamp/ventana anti-replay o controles del proveedor antes de
  exponer el endpoint a una integración no confiable.

La configuración general está en [Operación de Brokerdex](operations.md). La
política de secretos retirados está en
[Credenciales locales y rotación](security/credential-rotation.md).
