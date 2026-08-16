# Contribuir

1. Crea una rama descriptiva para tu cambio.
2. No incluyas entornos virtuales, ejecutables, configuraciones locales ni archivos de `versiones_anteriores`.
3. Antes de enviar, ejecuta lint, formato y tests (config en `pyproject.toml`):

```bat
python -m pip install ruff pytest
python -m ruff check .
python -m ruff format --check .
python -m pytest
```

4. Si modificas DSP, añade una prueba sintética reproducible en `tests/` y verifica forma, tipo y valores finitos.
5. Prueba con volumen bajo y documenta el dispositivo usado.
6. Describe claramente si el cambio fue validado solo de forma estática o también con audio real.
