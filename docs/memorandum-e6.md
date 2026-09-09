# Memorándum — dos estrategias validadas sobre E6

**Fecha:** 2026-09-09
**Instrumento:** E6 (futuro Euro FX, CME), contrato continuo ajustado por ratio
**Datos:** 6 145 287 barras de 1 minuto, 2008-01-02 a 2025-11-05 (FirstRate Data)
**Scripts:** `scripts/weekend_gap.py`, `scripts/regime_variance.py`, `scripts/pnl_series.py`

---

## Resumen ejecutivo

De todo lo probado, dos mecanismos superan costes y aguantan validación fuera de muestra:

| | ventaja/op | fuera de muestra | ops/año | neto anual s/ nominal |
|---|---|---|---|---|
| **Salto de fin de semana** | 5.2× coste | ambas mitades t>2.5 | ~52 | ~0.92 % |
| **Filtro ER sobre cruce slow×superSlow** | 2.9× coste | 7.6× → 2.9× | ~128 | ~2.27 % |

Ambas operan **en reversión**, no en tendencia. El sistema de cruce de medias
tal cual —en su dirección natural— pierde dinero de forma consistente.

Las cifras anuales son **sobre nominal** (137 500 USD por contrato), no sobre
capital comprometido.

---

## 1. Metodología común

### Convención de barras y ejecución

- **Señal:** barras de 15 minutos que cierran en `:59`, `:14`, `:29`, `:44`.
- **Ejecución:** al cierre de la primera barra de 1 minuto posterior, típicamente
  `:00`, `:15`, `:30`, `:45`. El 94.6 % de las ejecuciones cae exactamente al
  minuto siguiente; el resto son cortes de sesión, con el hueco anotado.
- Nunca se ejecuta al cierre que genera la señal. Medimos que hacerlo **infla el
  bruto un ~100 %** (comprobado sobre 60 semillas sintéticas): el cierre entra en
  la media rápida con peso `1/N_fast` y en la lenta con `1/N_slow`, así que el
  cruce se dispara preferentemente en barras cuyo propio cierre fue extremo.

### Unidades

Todo en **porcentaje de nominal, por transacción**. Dos razones:

- **Porcentaje**, porque el contrato continuo está ajustado por ratio: el ajuste
  preserva rendimientos pero **no** diferencias absolutas de precio. La rejilla de
  ticks mide 0.00012 en 2008 frente a 0.00005 hoy. Cualquier cifra en dólares
  calculada sobre diferencias de precio sobrestima los años iniciales.
- **Por transacción**, porque el spread se paga al rotar la posición, no en cada
  barra. La duración media de posición es ~15 barras, así que la cifra por barra
  subestima la ventaja en ese factor y no es comparable con el coste.

### Referencia de coste

| concepto | valor |
|---|---|
| Tick de E6 | 0.00005 = medio pip = **6.25 USD**/contrato |
| Spread supuesto | 1 tick, cruzado entero en cada reversión |
| **Coste spread** | **0.00455 %** de nominal |
| Comisiones (~2 USD round-turn) | 0.00145 % = 0.32 ticks |
| **Coste total con comisiones** | **0.00600 %** |

El coste en % **no es constante**: un tick es 0.0031 % de nominal con el euro a
1.60 y 0.0053 % a 0.95. El 0.00455 % es una estimación central a 1.10.

---

## 2. Estrategia A — Salto de fin de semana

### Mecanismo

1. En el cierre del viernes, medir el movimiento de los últimos **15-20 minutos**.
2. **Desvanecerlo**: si el precio subió hacia el cierre, ponerse **corto**; si
   bajó, **largo**.
3. Mantener durante el fin de semana.
4. Deshacer en la **apertura del domingo**.

### Por qué funciona: es una cuestión de escala, no de mejor señal

El salto medio en valor absoluto es **0.09597 %**, unas **21 veces el coste**.
Con un movimiento tan grande frente al spread, basta acertar el signo el
**52.4 %** de las veces para cubrir costes. Todo lo intradía que probamos peleaba
contra un spread del mismo orden de magnitud que la señal.

### Resultados (927 fines de semana)

| | valor |
|---|---|
| Bruto por fin de semana | +0.02378 % (t = **+3.78**) |
| Acierto de signo | 58.3 % (necesario: 52.4 %) |
| Ventaja | **5.23× el coste** |
| Neto tras spread | +0.0176 % → **~0.92 %/año** s/ nominal |
| Neto con comisiones | +0.0162 % → ~0.84 %/año |
| Años positivos | 12 de 18 |

### Validación

- **Ambas mitades independientemente significativas:** 1ª mitad t = +2.84,
  2ª mitad t = +2.57. Ninguna otra idea del proyecto logró esto.
- **Es una meseta, no un pico.** Todos los horizontes de entrada entre 10 minutos
  y 3 horas dan t entre 2.0 y 3.8 con acierto del 55-58 %. Se apaga a partir de
  4 horas. Un resultado sobreajustado sería un pico aislado.
- **Coherente con la microestructura ya medida:** la autocorrelación de lag 1 en
  barras de 15 min es −0.0213. Es la misma reversión de corto plazo, capturada
  donde mantenerla no cuesta rotación adicional.

### Salida: en la apertura

Se probó mantener más allá. Hasta +120 barras la media es nominalmente mayor
(+0.0272 % frente a +0.0238 %) pero la diferencia está **dentro de un error
típico**, así que no es real. A partir de +480 barras la segunda mitad se
desploma a cero y luego a negativo. El hueco se rellena parcialmente
(correlación −0.02 a −0.13 con el movimiento posterior), que es por qué mantener
lo destruye.

### Cautelas

- La desviación del salto es **0.186 %, ocho veces la ventaja media**. La varianza
  por operación es enorme; no se puede juzgar con un puñado de fines de semana.
- Se probaron 5 reglas antes del barrido de horizontes. La meseta y la consistencia
  entre mitades lo defienden, pero el sesgo de selección no es nulo.

---

## 3. Estrategia B — Filtro por ratio de eficiencia sobre el cruce slow×superSlow

### Las tres medias

Ninguna tiene parámetro de periodo; todas derivan su ventana de la estructura de
rachas de la propia serie.

| media | derivación | N medio en E6 |
|---|---|---|
| `fast` | rachas de los incrementos crudos | ~5 barras |
| `slow` | `N_slow(t) = N_fast(t) + N_fast(t − N_fast(t))` | ~10 barras |
| `superSlow` | esa regla iterada 3 niveles, ≈ 2³·N_fast | ~40 barras |

### El ratio de eficiencia (ER)

Ratio de eficiencia de Kaufman, calculado sobre los cierres de un tramo:

```
ER = |cierre_final − cierre_inicial| / Σ |cierre_i − cierre_i−1|
```

Distancia **neta** sobre distancia **total**. Acotado en [0, 1]:

- **ER ≈ 0** → el precio acabó donde empezó: tramo errático.
- **ER ≈ 1** → todos los pasos en la misma dirección: tramo direccional.

Es **invariante de escala** (cociente de diferencias), así que el ajuste por
ratio del contrato continuo no lo distorsiona. Calcularlo con retornos simples o
logarítmicos da resultados correlacionados al **0.999995**: la elección es
indiferente. Si se formaliza, preferir log-retornos, donde la invariancia es
exacta por construcción (`|Σr| / Σ|r|`) y no aproximada.

### Mecanismo

1. Un **tramo** va de un cruce `slow × superSlow` al siguiente. Su longitud es
   endógena por herencia (media 28 barras, mediana 22).
2. Al abrirse un tramo, calcular el ER del tramo **anterior**, ya cerrado.
   Esto es causal: el tramo previo está completo antes de que empiece el actual.
3. Si **ER < 0.055** (quintil más errático), operar este tramo. Si no, estar fuera.
4. Dirección: **reversión del cruce**.

| slow vs superSlow | el cruce dice | **se opera** |
|---|---|---|
| slow **por encima** | largo | **CORTO** |
| slow **por debajo** | corto | **LARGO** |

5. Mantener hasta el siguiente cruce, que cierra el tramo.

Como el tramo se define por que el signo de `slow − superSlow` no cambie, la
posición es **constante dentro del tramo**: exactamente **una operación por
tramo** (verificado: 0 de 14 935 tramos con más de una dirección).

### Resultados (operativa C, corte 0.055)

Descomposición por operación, segunda mitad:

| | % nominal | ticks | USD/contrato |
|---|---|---|---|
| **Bruto** | 0.01315 | 2.89 | +18.08 |
| Coste spread | −0.00455 | −1.00 | −6.26 |
| **Neto** | **0.00860** | **1.89** | **+11.82** |
| Neto con comisiones | 0.00714 | 1.57 | +9.82 |

Muestra completa, 2 305 operaciones en 18 años:

| | valor |
|---|---|
| Bruto acumulado | 54.66 % |
| Coste (spread) | 10.49 % |
| **Neto con comisiones** | **40.83 %** = 56 144 USD/contrato |
| Media anual | **2.27 %** s/ nominal |
| Años positivos | 12 de 18 |
| Operaciones/año | 113-146 (muy estable) |

### Validación fuera de muestra

Corte elegido **solo en la primera mitad**, aplicado sin tocar a la segunda:

| corte ER | C 1ª mitad | C 2ª mitad | ops | neto 2ª |
|---|---|---|---|---|
| 0.028 | 3.41× | **4.49×** | 587 | +9.32 % |
| 0.040 | 6.78× | 3.20× | 826 | +8.27 % |
| **0.055** | 7.55× | **2.89×** | 1 155 | **+9.93 %** |
| 0.083 | 5.74× | 1.02× | 1 728 | +0.16 % |
| 0.112 | 4.20× | 0.65× | 2 301 | −3.66 % |

Tres lecturas:

- Aguanta fuera de muestra con holgura (2.89× el coste). El encogimiento desde
  7.55× es el sesgo de selección esperado.
- **El corte más estrecho (0.028) es el único donde la segunda mitad supera a la
  primera** (3.41× → 4.49×). Un resultado sobreajustado siempre encoge; que aquí
  crezca apunta a efecto real.
- **A partir de 0.083 se cae por un precipicio**, no hay degradación suave. El
  efecto vive estrictamente en el quintil errático.

Los cortes 0.028 y 0.055 dan **casi el mismo beneficio total** (+9.32 % vs
+9.93 %) por caminos opuestos: pocas operaciones buenas frente a más operaciones
flojas. La elección es de robustez, no de rentabilidad; 0.055 gana por tener el
doble de operaciones para la misma ganancia.

La operativa B (`fast × superSlow`) también supera coste: 2.22× fuera de muestra,
1 486 operaciones, +8.25 %. La operativa A (`fast × slow`) **no supera coste con
ningún corte**.

### Cautelas

- **La concentración temporal es fuerte.** 2008 (+14.60 %) y 2022 (+8.01 %)
  aportan el **55 % del beneficio en el 11 % del tiempo**. Sin esos dos años la
  media anual cae a 1.14 %. Ambos son años de volatilidad extrema, lo cual encaja
  con la lógica de la estrategia, pero la rentabilidad depende de que aparezcan.
- **Rachas perdedoras largas.** 2017-2018 y 2020-2021 suman −7.69 %. Hay que poder
  aguantar dos a cuatro años negativos seguidos.
- **La segunda mitad es 5 años positivos y 5 negativos**, salvada por la magnitud
  de 2022. Más frágil de lo que sugiere el promedio.
- **Asimetría estructural:** los tramos se definen por el cruce de C, así que el
  filtro de régimen y la señal de C comparten reloj. Para B no ocurre.
- Sesgo de selección residual: "ER, cola baja, operativas B y C" se eligió mirando
  la muestra completa, aunque el umbral se validó honestamente.

---

## 4. Lo que NO funcionó

Registrado para no volver a proponerlo.

| idea | resultado |
|---|---|
| **Bandas muertas** (todas las variantes) | Ninguna mejora la ventaja/op. Filtrar a divergencias **grandes** la empeora (0.50× → 0.34×). Dos causas: la reversión **no** está concentrada en divergencias grandes, y una banda **no reduce operaciones**, intercala salidas a plano que cuestan rotación de ida y vuelta. |
| **Ratio de varianza VR(2) como régimen** | Las dos mitades se contradicen y varias celdas cambian de signo. Causa de muestra pequeña: VR(2) exige dos estimaciones de varianza sobre ~25 retornos, imposible en un tramo de 26 barras. |
| **Régimen de 8 estados por signo de PnL** | Solo 1.23× el coste fuera de muestra, frente a 2.2-2.9× del ER. Clasificar por el resultado de ~2 operaciones es un proxy ruidoso frente a medir todas las barras del tramo. |
| **Dirección tendencia**, en cualquier variante | Pierde consistentemente. Las tres operativas pierden en tendencia y ganan lo mismo invertidas. |
| **"Corto todos los fines de semana"** | 1.93× coste pero t = +1.44, no significativo. El signo sí está sesgado (42.5 % positivos, −4.6 σ) pero la media no lo sigue: muchos saltos bajistas pequeños y pocos alcistas grandes. |
| **"Seguir el movimiento del último día"** antes del fin de semana | 1ª mitad t = +2.29, 2ª mitad t = −0.53. Sobreajuste de manual. |

### El techo estructural del intradía

La reversión intradía es **real pero unas 4 veces menor que el spread**:

- Autocorrelación de lag 1 en barras de 15 min: **−0.0213** (aplastantemente
  significativa con 422 152 observaciones).
- Ratios de varianza 0.948-0.978, mínimo en 20 barras (5 horas).
- La regla ingenua de desvanecer la barra previa: 0.00255 %/op = **0.56× el coste**.

**Ninguna mejora de la señal puede multiplicar la ventaja por 4**: el techo lo fija
la estructura de autocorrelación. El sistema endógeno con filtro de régimen ya
extrae más del doble que la regla ingenua. Por eso las dos estrategias que
funcionan lo hacen por **escala** (el salto es 21× el coste) o por **selección**
(operar solo el 20 % de tramos favorables), no por refinar la señal.

---

## 5. Siguientes pasos

1. **Ejecución pasiva.** Es la única palanca capaz de cambiar el resultado por un
   factor grande. Una estrategia de reversión es la candidata natural a órdenes
   limitadas: estás comprando cuando el precio ha caído. Pasar de **pagar** el
   spread a **cobrarlo** vale hasta 2 ticks. Con comisiones incluidas, hoy el 84 %
   del bruto se lo comen los costes. Requiere simular tasas de relleno con los
   datos de 1 minuto y modelar la selección adversa.
2. **Combinar ambas estrategias.** Son independientes en su lógica y casi en su
   calendario. Falta medir la correlación de sus series de resultados.
3. **Datos sin ajustar.** Permitirían contabilidad en dólares reales y estudiar si
   el umbral en % —que depende del nivel de precio— afecta a la rentabilidad.
4. **Dimensionamiento.** Las cifras son sobre nominal. Con años de −3.4 % y rachas
   de cuatro años negativos, apalancar agresivamente sería temerario.

---

## Anexo — reproducir los resultados

```
# Extraer y preparar datos (una vez)
.venv/Scripts/python.exe scripts/prepare_bars.py \
    --input data/E6_full_1min_continuous_ratio_adjusted.txt \
    --output data/E6_15min.csv

# Series de PnL por barra para las tres operativas
.venv/Scripts/python.exe scripts/pnl_series.py \
    --csv data/E6_15min.csv --slow-mode lagged_fast \
    --output data/E6_pnl_lagged_fast.csv

# Estrategia A
.venv/Scripts/python.exe scripts/weekend_gap.py \
    --data data/E6_full_1min_continuous_ratio_adjusted.txt --entry-window 15

# Estrategia B
.venv/Scripts/python.exe scripts/regime_variance.py \
    --pnl data/E6_pnl_lagged_fast.csv
```

Los ficheros de `data/` están en `.gitignore` (343 MB el de 1 minuto). Se
regeneran con los comandos de arriba a partir del zip original.
