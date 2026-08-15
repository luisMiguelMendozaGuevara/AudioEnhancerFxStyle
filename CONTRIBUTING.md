# Contribuir

1. Crea una rama descriptiva para tu cambio.
2. No incluyas entornos virtuales, ejecutables, configuraciones locales ni archivos de `versiones_anteriores`.
3. Ejecuta la compilación sintáctica:

```bat
python -m py_compile AudioEnhancer_FxStyle.py
```

4. Si modificas DSP, añade una prueba sintética reproducible y verifica forma, tipo y valores finitos.
5. Prueba con volumen bajo y documenta el dispositivo usado.
6. Describe claramente si el cambio fue validado solo de forma estática o también con audio real.
