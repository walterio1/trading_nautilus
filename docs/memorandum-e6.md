# Memorándum — dos estrategias validadas sobre E6

**Fecha:** 2026-09-09
**Instrumento:** E6 (futuro Euro FX, CME), contrato continuo ajustado por ratio
**Datos:** 6 145 287 barras de 1 minuto, 2008-01-02 a 2025-11-05 (FirstRate Data)
**Scripts:** `scripts/weekend_gap.py`, `scripts/regime_variance.py`, `scripts/pnl_series.py`

> **⚠ ADDENDUM 2026-09-14 — leer antes que el resto.** La validación estricta
> con ventana creciente (sección 6) **no confirma la estrategia B**: su resultado
> fuera de muestra cae a +0.03 / +0.37 %/año. **La estrategia A sí la supera**
> (+0.57 %/año, 5 de 5 bloques positivos). Además, el coste real de 2008-2015
> era 1.5-2× el supuesto aquí. El cuerpo del documento se conserva tal como se
> escribió el 2026-09-09; las correcciones están en la sección 6.
>
> **Segunda revisión, misma fecha (sección 7):** con LOTO y desglose anual, la
> estrategia A con **una sola ventana** gana +0.36 %/año y **no es estable** (9 de
> 18 años positivos). **Combinando todas las ventanas** sube a +0.58-0.70 %/año y
> 11-12 de 18 años positivos, sin elegir nada con los datos salvo la dirección.

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

## 6. Addendum 2026-09-14 — validación estricta y coste real

Motivado por las tareas anotadas tras escribir este memorándum: las elecciones
de la estrategia B (operar C, descartar A y B, cortar el ER en 0.055) se hicieron
**mirando la muestra entera**, así que son exógenas. Se validan de dos formas:

- **Opción 1 — LOTO** (leave one tenth out): 10 bloques; en cada uno se elige
  la configuración con el 90 % restante y se opera el 10 % apartado. Tiene un
  pequeño look-ahead: entrena también con datos posteriores al bloque.
- **Opción 2 — ventana creciente**: entrenar con 0-50 %, operar 50-60 %;
  entrenar con 0-60 %, operar 60-70 %… Estrictamente fuera de muestra.

En ambas, el optimizador elige **dentro de la ventana de entrenamiento** entre
432 combinaciones: operativa (A/B/C) × ventana del ER (tramo previo, N_fast,
N_slow, N_super, 2·N_super, día, semana, mes) × cuantil de corte × dirección.

Script: `scripts/loto_e6.py`.

### 6.1 El coste real no era constante

Medida la rejilla de ticks de la propia serie año a año (el menor incremento que
se repite en ≥1 % de barras, dividido por el precio medio; el factor del ajuste
por ratio se cancela en el cociente):

| periodo | rejilla | coste real | vs 0.00455 % |
|---|---|---|---|
| 2008-2015 | 0.00012 | 0.0068-0.0090 % | **1.50-1.97×** |
| 2016 | 0.00006 | 0.0045 % | transición |
| 2017-2025 | 0.00005 | 0.0037-0.0045 % | 0.81-0.99× |

El tick efectivo del E6 se **partió por la mitad en 2016**. Los primeros ocho
años costaban bastante más de lo supuesto; los últimos nueve, algo menos.

Impacto, pequeño en ambas estrategias porque el bruto domina:

| | coste fijo | coste real |
|---|---|---|
| A — salto de fin de semana | +0.91 %/año | **+0.85 %/año** (t = +2.71) |
| B — ER, muestra completa | +44.17 % | **+41.52 %** |

(Solo spread, sin comisiones.)

### 6.2 Estrategia B: el LOTO la confirma, la ventana creciente no

**LOTO (opción 1):** en los **10 de 10 pliegues** el optimizador elige
exactamente la configuración del memorándum — **C · tramo previo · cuantil 0.20
(corte 0.054-0.055) · reversión**. Resultado: **+40.32 % = +2.24 %/año**, 9 de 10
pliegues positivos. Las elecciones "exógenas" son las que los datos eligen solos.

Sobre usar la **mediana** como corte (cuantil 0.50): es peor. En el único pliegue
donde el optimizador la eligió perdió −10.00 %. El corte bueno es el cuantil 0.20.

**Ventana creciente (opción 2), estrictamente fuera de muestra:**

| objetivo | neto últimos ~9 años | %/año | bloques + |
|---|---|---|---|
| neto total | +0.24 % | **+0.03** | 3 / 5 |
| t del neto | +3.36 % | **+0.37** | 3 / 5 |

**La ventaja desaparece.** No porque la configuración deje de funcionar —la misma
configuración fija (C, cuantil 0.20) rinde +9.28 % en esos mismos bloques, ~1 %/año—
sino porque **con solo el pasado no se habría elegido de forma fiable**. En 3 de 5
bloques, entrenar con los datos disponibles eligió un corte más flojo (cuantil
0.30, corte ≈0.083) o una ventana semanal, y esas elecciones perdieron.

Es la consecuencia práctica del precipicio ya visto en la sección 3: la zona
rentable es estrecha (cuantil ≤ 0.20) y justo fuera de ella se pierde. **Un
óptimo en filo de navaja no se estima bien en tiempo real.** El "pequeño pecado"
del LOTO no es pequeño aquí: es la diferencia entre +2.24 %/año y casi cero.

La concentración temporal persiste con coste real: 2008 aporta el 35.5 % del
total y 2008 + 2022 el 55 %.

### 6.3 Estrategia A: supera la ventana creciente

Mismo criterio aplicado al salto de fin de semana, eligiendo la ventana de
entrada (5 min a 6 h) solo con el pasado y con coste real por año:

| bloque operado | ventana elegida | neto |
|---|---|---|
| 50-60 % | 20 min | +0.55 % |
| 60-70 % | 20 min | +2.41 % |
| 70-80 % | 20 min | +0.47 % |
| 80-90 % | 20 min | +0.98 % |
| 90-100 % | 20 min | +0.68 % |
| **total ~9 años** | | **+5.09 % = +0.57 %/año, 5 / 5 bloques +** |

La elección es **estable** (20 minutos en los cinco bloques, con los dos
objetivos) y todos los bloques ganan. Es lo esperable de la meseta: si
cualquier ventana entre 10 min y 3 h funciona, elegir con datos parciales no
puede equivocarse mucho.

### 6.4 Conclusión revisada

| | criterio estricto (ventana creciente) | veredicto |
|---|---|---|
| **A — salto de fin de semana** | +0.57 %/año, 5/5 bloques, elección estable | **validada** |
| **B — filtro ER** | +0.03 a +0.37 %/año, 3/5 bloques, elección inestable | **no validada en tiempo real** |

El orden del resumen ejecutivo **se invierte**: el 2.27 %/año de B era
rentabilidad de una configuración elegida conociendo el futuro; la cifra
alcanzable operando es la de A.

Lo que sí queda de B: la configuración tiene contenido real (el LOTO la elige
10/10 y rinde ~1 %/año en la segunda mitad con corte fijo). Si se quisiera
rescatar, el trabajo pendiente es **hacer la elección robusta**, no afinarla:
por ejemplo fijar el cuantil 0.20 a priori y no reoptimizarlo, o promediar
cortes vecinos en vez de quedarse con el mejor.

---

## 7. Addendum 2026-09-14 (b) — salto de fin de semana: LOTO, estabilidad anual y combinación de ventanas

Script: `scripts/weekend_gap.py` (secciones de validación al final de la salida).
Coste real por año; un viernes sin movimiento en la ventana no se opera.

### 7.1 LOTO sobre ventana y dirección

En cada pliegue se eligen **a la vez** la ventana de entrada (5 min a 6 h) y la
dirección (desvanecer o seguir) con el 90 % restante, porque "desvanecer" también
salió de mirar la muestra.

| | resultado |
|---|---|
| Regla fija de 15 min, elegida mirando todo | +0.93 %/año, t = +2.98 |
| **LOTO** | **+0.36 %/año**, 7/10 pliegues positivos |
| Ventana creciente (últimos 8.9 años) | +0.60 %/año, 5/5 bloques |

- **La estructura es robusta:** desvanecer se elige en 10/10 pliegues y la ventana
  cae siempre en la meseta 15-60 min (6× 20 min, 3× 15, 1× 60). Con los dos
  objetivos de selección las elecciones son idénticas.
- **El dinero no:** menos de la mitad que la regla fija. Casi toda la diferencia
  está en el pliegue 1 (2008-09): sin ese periodo se eligió 60 min y perdió −3.71 %.

### 7.2 Estabilidad año a año con una sola ventana: mala

- **9 de 18 años positivos**, año mediano **+0.01 %**, peor año 2008 (−2.84 %).
- **Los dos mejores años (2011 +4.09 %, 2020 +2.74 %) suman el 107 % del total**:
  el resto de años en conjunto pierde.
- La regla fija de 15 min parecía estable (12/18, mediana +0.36 %) solo porque se
  eligió conociendo el resultado.

### 7.3 Combinar ventanas en vez de elegir una

La pérdida del LOTO viene de **qué** ventana de la meseta toca en cada pliegue.
Combinarlas elimina esa elección. Para no colar otra elección hecha mirando los
datos, las combinaciones son fijas sobre **todas** las ventanas, o se seleccionan
dentro de cada 90 % de entrenamiento:

| variante | %/año | pliegues + | años + | año mediano | peor año | 2 mejores años | ventana creciente |
|---|---|---|---|---|---|---|---|
| una ventana (LOTO) | +0.36 | 7/10 | 9/18 | +0.01 | −2.84 | 107 % | +0.60, 5/5 |
| **voto, todas** | **+0.70** | 7/10 | 10/18 | +0.28 | −2.29 | 66 % | +0.75, 5/5 |
| **media, todas** | +0.58 | 7/10 | 11/18 | +0.35 | −2.08 | 58 % | +0.60, 5/5 |
| media, positivas en entrenamiento | +0.64 | 7/10 | **12/18** | **+0.40** | −2.08 | **56 %** | +0.61, 5/5 |
| media, 5 mejores en entrenamiento | +0.59 | 7/10 | 12/18 | +0.30 | **−1.77** | 66 % | +0.53, 5/5 |

- **Todas las combinaciones casi duplican** la ventana única y **mejoran la
  estabilidad** en todas las métricas: más años positivos, año mediano claramente
  positivo, y el beneficio deja de depender de dos años.
- **Voto y media sobre todas las ventanas no eligen nada con los datos** salvo la
  dirección, que el LOTO fijó 10/10. Son las más honestas.
- **Voto** maximiza el %/año; **media sobre las positivas** es la más estable.
  "Media" opera fraccionalmente: posición grande cuando las ventanas coinciden,
  pequeña cuando discrepan, con el coste escalado al tamaño.
- **Lo que no se arregla:** 2010 pierde en todas las variantes (−1.8 a −2.3 %), y
  2008-2010 sigue siendo un tramo malo.

### 7.4 Conclusión revisada de la estrategia A

La ventaja existe y la dirección es inequívoca, pero su tamaño realista es
**~0.6-0.7 %/año sobre nominal** y con años perdedores frecuentes. La forma de
operarla es **combinando ventanas**, no apostando por una. La regla fija de
15-20 min de la sección 2 sobreestimaba el resultado.

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
