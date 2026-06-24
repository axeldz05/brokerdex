---
name: django-tdd
description: Strict Test-Driven Development (TDD) patterns for Django, Django REST Framework, and Celery async workers. Focuses on hermetic state, fast execution, and avoiding flaky tests in asynchronous workflows.
---

# Django & Celery TDD Patterns

## Core Philosophy
Never write a line of business logic, endpoint handler, or task worker without a pre-existing failing test (Red phase). Write the minimum code necessary to make the test pass (Green phase), then apply semantic compression to remove structural duplication (Refactor phase).

## Rules

1. **Enforce Hermetic Database States**
   - Every test that touches models or triggers database storage must be decorated with `@pytest.mark.django_db`.
   - Never rely on data surviving between independent test runs. Use clean setups.

2. **No Hardcoded Paths or Environment Dependencies**
   - Use pytest's built-in `tmp_path` fixture if a task or service needs to generate local files (like transaction logs or CSV exports).

3. **Isolate External Network IO**
   - Mock all downstream HTTP/API requests using tools like `responses` or `unittest.mock.patch`. A test must never attempt to reach an external network.

4. **Deterministic Celery Task Testing**
   - **Unit Level (Synchronous):** When testing the business logic inside a Celery task, force immediate execution by setting `CELERY_TASK_ALWAYS_EAGER = True` in the test context.
   - **Integration Level (Asynchronous Concurrency):** When validating race conditions or actual message broker behavior:
     - **PROHIBIDO** usar `time.sleep()` fijos para esperar a que el worker procese el evento.
     - **OBLIGATORIO:** Implementar un loop de reintentos cortos (*controlled polling loop*) con límites estrictos de tiempo de espera (timeout) para verificar el cambio de estado en la base de datos.

---

## Code Templates & Refactoring Examples

### 1. Standard API & Model TDD (Eager Mode)

When drafting an order execution endpoint, write the test first using DRF's `APIClient`:

```python
import pytest
from decimal import Decimal
from django.urls import reverse
from rest_framework import status

@pytest.mark.django_db
def test_user_can_place_limit_order_with_sufficient_funds(api_client, user_wallet, creature):
    url = reverse('marketplace:order-list')
    data = {
        "creature_id": creature.id,
        "order_type": "LIMIT",
        "side": "BUY",
        "price": "150.00",
        "quantity": "1.0000"
    }
    
    # Action
    response = api_client.post(url, data, format='json')
    
    # Assertions
    assert response.status_code == status.HTTP_201_CREATED
    assert response.data['status'] == 'OPEN'
```
2. Async Task Polling Pattern (Anti-Flaky)

When testing that the asynchronous Battle Engine successfully mutates a creature's price, use a smart waiting loop instead of a hard halt:
```python
import time
import pytest
from decimal import Decimal

@pytest.mark.django_db
def test_battle_engine_updates_creature_price_async(creature):
    from simulator.tasks import process_battle_result
    
    initial_price = creature.current_price
    
    # Trigger task asynchronously (assuming Celery worker is running in test env)
    process_battle_result.delay(creature_id=creature.id, result="WIN")
    
    # Controlled Polling Loop (Max 2 seconds, checking every 0.1s)
    timeout = 2.0
    start_time = time.time()
    updated = False
    
    while time.time() - start_time < timeout:
        creature.refresh_from_db()
        if creature.current_price != initial_price:
            updated = True
            break
        time.sleep(0.1)
        
    assert updated is True
    assert creature.current_price > initial_price
```

## What to Avoid
- No Premature Architecture: Do not build complex BaseTest classes or mock layers before you have at least two clear instances of duplicated test configurations.
- No Assertions on Uncontrolled Data: Avoid asserting specific timestamps or database primary keys (id=1). Always match structural attributes or use relative matching (assert order.created_at is not None).

