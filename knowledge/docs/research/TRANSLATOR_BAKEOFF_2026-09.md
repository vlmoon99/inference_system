# Translator bake-off — 2026-09-27 (docs/AGENTIC_PLAN.md §2.5)

Run on the Spark: 100 fixed English captions (`backend/scripts/bakeoff/captions_en.json`, 25 local businesses × post/short, brand names as glossary), the product's own `translate.copy` prompt for Qwen, Hy-MT2's own structured-data prompt for Hy-MT2 (`|mt` mode). Scored by the MetricX-24 gate (:8012) and a Qwen judge with thinking (fluency · fidelity · tone · glossary, 1–5). Harness: `backend/scripts/translate_bakeoff.py`.

Hy-MT2-7B ran as a throwaway vLLM container on the Spark beside the production stack (latency is therefore not comparable: it shared the GPU with the judge and used no guided decoding). Hy-MT2-30B-A3B-FP8 and Lapa wait for the RTX box (`inference/hosts/rtx3090x3/bakeoff.sh`).

## Verdict so far

* **Hy-MT2-7B leads on every automatic measure**: MetricX mean 2.71 (ru) / 3.20 (uk) vs Qwen 3.50 / 3.97, p90 4.7 / 5.5 vs 6.2 / 6.4; judge fidelity 4.15 / 4.34 vs 3.96 / 4.11; glossary 5.0 (never touched a brand name) vs 4.8–5.0.
* **Fluency is a wash**: 2.77 / 2.82 vs 3.05 / 2.84 — both models write stiff Ukrainian; this is the fine-tune target, not a model choice.
* **Thinking buys nothing**: Qwen with reasoning is 4× slower and not better.
* One Hy-MT2 uk failure (missing field) out of 200; zero for Qwen.
* Decision waits for the owner sample below and the 30B-A3B row. If the eye agrees: ship Hy-MT2-7B as `TRANSLATE_BASE_URL/MODEL` (a fifth service, ~15 GB bf16, Apache-2.0) and keep MetricX as the gate.

MetricX: 0 = perfect, 25 = worst (error score, lower is better). Judge: 1-5, higher is better.

| candidate | lang | n | fail | MetricX mean | MetricX p90 | judge fluency | fidelity | tone | glossary | latency p50 s |
|---|---|---|---|---|---|---|---|---|---|---|
| hymt2-7b | ru | 100 | 0 | 2.71 | 4.716 | 2.77 | 4.15 | 3.46 | 5.0 | 32.46 |
| hymt2-7b | uk | 100 | 1 | 3.2 | 5.549 | 2.82 | 4.34 | 3.65 | 5.0 | 35.37 |
| qwen | ru | 100 | 0 | 3.5 | 6.207 | 3.05 | 3.96 | 3.63 | 4.81 | 5.14 |
| qwen | uk | 100 | 0 | 3.97 | 6.379 | 2.84 | 4.11 | 3.66 | 4.97 | 6.6 |
| qwen-think | ru | 100 | 0 | 3.48 | 5.773 | 3.08 | 3.88 | 3.67 | 4.89 | 24.33 |
| qwen-think | uk | 100 | 0 | 4.03 | 6.684 | 2.99 | 4.12 | 3.75 | 4.92 | 25.87 |

Worst judged problems (fidelity ≤ 2):

- qwen uk #13: Critical factual error: the date is translated as May 25th instead of May 20th, and the caption contains a grammatical error ('вільного англійського').
- qwen uk #95: Critical factual error: the date in the voiceover is changed from November 30th to December 6th, contradicting the caption and source text.
- qwen ru #50: Brand name 'Kiev Crumb' is not preserved as instructed, and 'sourdough' is mistranslated as a raw ingredient rather than the product 'sourdough bread/rolls'.
- qwen ru #57: The caption begins with Chinese characters ('Always wonder...') instead of Russian, rendering it completely unreadable for the target audience.
- qwen ru #64: The translation incorrectly translates 'white oak' as 'белый дуб' (white birch) instead of 'белый дуб' (white oak), and mistranslates the product name 'Willow' as 'Вяз' (elm) instead of 'Ива' (willow).
- qwen ru #95: The voiceover omits the specific date range (Nov 30 - Dec 6) and mistranslates 'won't last long' as 'won't go unnoticed'.
- qwen-think uk #13: Critical factual error: the date is changed from May 20th to May 25th, and the voiceover contains severe grammatical errors and broken syntax.
- qwen-think uk #57: The caption incorrectly translates 'Swipe' as 'Like' (Лайкніть), which is a critical functional error for Instagram/TikTok UI instructions.
- qwen-think uk #64: Brand name 'OakHaven' is mistranslated as 'Вілла' (Villa) instead of being kept as 'OakHaven', and 'Willow' is also incorrectly translated as 'Вілла' instead of 'Вілла' or 'Верба'.
- qwen-think uk #82: Translation introduces a 'pre-holiday rush' (передсвяткова метушня) which is not in the source text, and translates hashtags into Ukrainian instead of keeping the original English tags.
- qwen-think ru #0: Brand name 'Kyiv Crumb' was not preserved as instructed, and hashtags were translated instead of kept in English.
- qwen-think ru #49: Hashtags were translated instead of preserved, and 'tax prep' was expanded to 'tax returns' which is not in the source.
- qwen-think ru #57: The caption begins with Chinese characters instead of Russian, rendering the text completely broken and unreadable for the target audience.
- qwen-think ru #64: The translation contains a glaring error with the untranslated word 'craftsmanship' and mistranslates the product name 'Willow' as 'Вербена' (Vervain/Lemon Balm) instead of 'Ива' (Willow).
- qwen-think ru #90: The translation mistranslates 'nutrition guide' as 'training trial' (пробную тренировку), creating a logical contradiction with 'personalized nutrition' and misrepresenting the offer.
- qwen-think ru #95: The voiceover omits the specific date range (Nov 30 - Dec 6) present in the English source, reducing it to a vague 'Monday to Sunday'.
- hymt2-7b uk #23: The translation incorrectly changes the day of the week from Tuesday to Monday in both the caption and voiceover.
- hymt2-7b uk #71: The caption omits the 'flight' specification and mistranslates 'five PM' as '17:00' while the voiceover correctly says 'fifty guests', creating a factual contradiction and loss of detail.
- hymt2-7b uk #95: The voiceover contains a critical factual error, changing the start date from November 20th to the 30th, and uses unnatural phrasing like 'відкрили для продажу' (opened for sale) instead of 'представили' (presented/listed).
- hymt2-7b ru #53: The voiceover translation contradicts the English source by claiming the price is 450 UAH starting Jan 15, whereas the English states the previous price was 450 UAH and implies a new, lower price is now effective.
- hymt2-7b ru #54: The translation contains a critical factual error ('24/7' instead of 'extended evening hours') and a grammatical mistake ('в нашем студии').
- hymt2-7b ru #57: The translation incorrectly uses 'фрикадельки' (meatballs) instead of 'котлеты' (patties), fundamentally misrepresenting the product type.
- hymt2-7b ru #72: The translation incorrectly changes 'story' to 'video', which is a factual error regarding the platform feature.
- hymt2-7b ru #95: Critical factual error: the voiceover date is mistranslated as November 30th instead of the source's November 20th, and the caption uses 'dream home' (дом) instead of 'flat' (квартира).
- hymt2-7b ru #97: The translation incorrectly interprets 'soft fabrics' as 'мягкие ткани' (soft textiles/materials) instead of 'мягкая одежда' (soft clothing), and adds a negative condition about stock not yet arriving that contradicts the urgency of the original text.

# Owner sample — read these as the customer would

## Russian

### #0 · a Kyiv bakery selling sourdough and cinnamon rolls (post)

**English:** Olga brought her daughter to Kyiv Crumb for the first time last Tuesday, October 24th. She was nervous about the gluten-free options, but our staff guided her with care. As they sat by the window, the aroma of fresh sourdough and warm cinnamon rolls filled the air. Her daughter’s eyes lit up at the first bite, and Olga smiled with genuine relief. Moments like these remind us why we bake with heart. We are honored to be part of your city's daily rituals. Come taste the love in every crust and swirl.

**hymt2-7b** (MetricX 4.275): В прошлый вторник, 24 октября, Ольга впервые привела свою дочь в Kyiv Crumb. Она нервничала из-за безглютеновых вариантов блюд, но наши сотрудники терпеливо помогли ей. Когда они сидели у окна, воздух наполнился ароматом свежего хлеба из закваски и теплых рулетов с корицей. Глаза её дочери засветились с первого кусочка, а Ольга с облегчением улыбнулась. Такие моменты напоминают нам, почему мы испекаем с любовью. Для нас большая честь быть частью повседневных ритуалов вашего города. Приходите и почувствуйте любовь, заложенную в каждую корочку и начинку.

**qwen** (MetricX 3.113): В прошлый вторник, 24 октября, Ольга впервые привела свою дочь в Kyiv Crumb. Она переживала из-за наличия безглютеновых опций, но наш персонал окружил её заботой и вниманием. Пока они сидели у окна, воздух наполнялся ароматом свежего заквасочного хлеба и тёплых булочек с корицей. Глаза её дочки засияли с первого кусочка, а Ольга улыбнулась, испытывая искреннее облегчение. Такие моменты напоминают нам, почему мы печём с душой. Нам честь быть частью ежедневных ритуалов вашего города. Приходите, чтобы почувствовать любовь в каждой корочке и каждом завитке.

**qwen-think** (MetricX 3.624): В прошлый вторник, 24 октября, Ольга впервые привела свою дочь в Kyiv Crumb. Она переживала насчёт безглютеновых опций, но наши сотрудники окружили её заботой и вниманием. Пока они сидели у окна, воздух наполнился ароматом свежего заквасочного хлеба и тёплых булочек с корицей. Глаза её дочки засветились от первого кусочка, а Ольга улыбнулась, испытывая искреннее облегчение. Такие моменты напоминают нам, почему мы печём с душой. Нам честь быть частью ежедневных ритуалов вашего города. Приходите, чтобы почувствовать любовь в каждой корочке и каждом завитке.


### #8 · a mobile phone repair shop (post)

**English:** Cracked screen? Don't panic! At SnapFix, we’re offering a free consultation for first-time customers. Bring your device in before October 15th and let our experts assess the damage with zero obligation. We’ll give you an honest quote and timeline so you know exactly what to expect. No hidden fees, no pressure—just quick, reliable repairs. Stop by today and let’s get your phone back to life. #SnapFix

**hymt2-7b** (MetricX 2.83): Повреждённый экран? Не паникуйте! В SnapFix мы предлагаем бесплатную консультацию для новых клиентов. Принесите свой устройство до 15 октября, и наши специалисты оценят степень повреждения без каких-либо обязательств. Мы предоставим вам честную смету и сроки выполнения работ, чтобы вы точно знали, чего ожидать. Никаких скрытых платежей, никакого давления — только быстрые и надёжные ремонты. Заходите уже сегодня, и мы вернём ваш телефон к жизни. #SnapFix

**qwen** (MetricX 1.265): Разбитый экран? Не паникуйте! В SnapFix мы предлагаем бесплатную консультацию для новых клиентов. Принесите устройство до 15 октября, и наши эксперты оценят повреждения без каких-либо обязательств. Мы дадим честную оценку стоимости и сроки ремонта, чтобы вы точно знали, чего ожидать. Никаких скрытых платежей, никакого давления — только быстрые и надёжные ремонтные работы. Заходите сегодня, и мы вернём вашему телефону жизнь. #SnapFix

**qwen-think** (MetricX 3.865): Разбитый экран? Не паникуйте! В SnapFix мы предлагаем бесплатную консультацию для новых клиентов. Принесите своё устройство до 15 октября, и наши эксперты оценят повреждения без каких-либо обязательств. Мы дадим честную смету и сроки, чтобы вы точно знали, чего ожидать. Никаких скрытых платежей, никакого давления — только быстрые и надёжные ремонты. Заходите сегодня, и мы вернём вашему телефону жизнь. #SnapFix


### #16 · a nail salon (post)

**English:** Ever wondered what happens between your appointment? Here is a behind-the-scenes peek at our daily routine! We meticulously sanitize every tool and station to ensure your safety and comfort. Our team prepares fresh supplies and reviews hygiene protocols before opening doors at 9 AM on Monday. This dedication to cleanliness is what makes LuxeNails your trusted spot for flawless manicures and pedicures. We believe that great nails start with a sterile, professional environment. Come see the care we put into every detail. Book your next appointment today and experience the difference! #NailSalon

**hymt2-7b** (MetricX 1.837): Вы когда-нибудь задумывались о том, что происходит во время вашего визита? Вот краткий взгляд за кулисы на нашу ежедневную работу! Мы тщательно дезинфицируем каждый инструмент и рабочее место, чтобы обеспечить вашу безопасность и комфорт. Наша команда готовит свежие принадлежности и проверяет правила гигиены перед открытием дверей в понедельник в 9 часов утра. Именно такая преданность чистоте делает LuxeNails вашим надежным местом для идеальных маникюров и педикюров. Мы считаем, что прекрасные ногти начинаются с стерильной и профессиональной среды. Приходите и увидите, какое внимание мы уделяем каждой детали. Забронируйте свой следующий визит уже сегодня и почувствуйте разницу! #NailSalon

**qwen** (MetricX 1.836): Всегда хотели узнать, что происходит между вашими визитами? Загляните за кулисы нашего ежедневного расписания! Мы тщательно дезинфицируем каждый инструмент и рабочее место, чтобы обеспечить вашу безопасность и комфорт. Наша команда готовит свежие расходные материалы и проверяет протоколы гигиены перед открытием дверей в 9 утра в понедельник. Именно эта преданность чистоте делает LuxeNails вашим надежным местом для безупречных маникюров и педикюров. Мы верим, что красивые ногти начинаются со стерильной и профессиональной обстановки. Приходите и оцените заботу, которую мы вкладываем в каждую деталь. Запишитесь на следующий визит уже сегодня и почувствуйте разницу! #NailSalon

**qwen-think** (MetricX 1.482): Всегда хотели узнать, что происходит в салоне между вашими визитами? Загляните за кулисы нашей ежедневной рутины! Мы тщательно дезинфицируем каждый инструмент и рабочее место, чтобы обеспечить вашу безопасность и комфорт. Наша команда готовит свежие расходные материалы и проверяет протоколы гигиены перед открытием дверей в 9 утра в понедельник. Именно эта преданность чистоте делает LuxeNails вашим надежным местом для безупречных маникюров и педикюров. Мы верим, что идеальные ногти начинаются со стерильной и профессиональной обстановки. Приходите и оцените заботу, которую мы вкладываем в каждую деталь. Запишитесь на свой следующий визит уже сегодня и почувствуйте разницу! #NailSalon


### #24 · an accounting firm for small businesses (post)

**English:** Ready to scale your small business without the financial stress? LedgerLeap is here to simplify your numbers so you can focus on growth. To celebrate our launch, we’re offering an exclusive 20% discount on our foundational accounting packages. This special offer is only available until Friday, so don't wait to secure your spot. Our team specializes in helping entrepreneurs like you navigate tax season, bookkeeping, and cash flow management with confidence. Click the link in our bio to claim your discount today. Let’s make your finances work as hard as you do.

**hymt2-7b** (MetricX 1.405): Готовы расширять свой малый бизнес без финансового стресса? LedgerLeap поможет упростить ведение учета, чтобы вы могли сосредоточиться на развитии. В честь запуска мы предлагаем эксклюзивную скидку 20% на наши базовые учетные пакеты. Эта специальная акция действует только до пятницы, поэтому не откладывайте и забронируйте свое место прямо сейчас. Наша команда специализируется на помощи предпринимателям, подобным вам, в уверенном преодолении налогового периода, ведении бухгалтерского учета и управлении денежными потоками. Нажмите на ссылку в нашем описании, чтобы получить скидку уже сегодня. Давайте сделаем так, чтобы ваши финансы работали столь же усердно, как и вы сами.

**qwen** (MetricX 3.975): Готовы масштабировать свой малый бизнес без финансового стресса? LedgerLeap упростит ваши цифры, чтобы вы могли сосредоточиться на росте. В честь запуска мы предлагаем эксклюзивную скидку 20% на наши базовые пакеты бухгалтерских услуг. Это специальное предложение действует только до пятницы, поэтому не откладывайте и забронируйте свое место. Наша команда специализируется на помощи предпринимателям, таким как вы, в прохождении налогового сезона, ведении бухгалтерского учета и управлении денежными потоками с уверенностью. Нажмите на ссылку в нашем профиле, чтобы воспользоваться скидкой прямо сейчас. Давайте заставим ваши финансы работать так же усердно, как и вы.

**qwen-think** (MetricX 3.806): Готовы масштабировать малый бизнес без финансового стресса? LedgerLeap упростит ваши цифры, чтобы вы могли сосредоточиться на росте. В честь запуска мы предлагаем эксклюзивную скидку 20% на наши базовые пакеты бухгалтерских услуг. Это специальное предложение действует только до пятницы, поэтому не откладывайте и забронируйте свое место. Наша команда специализируется на помощи предпринимателям, подобным вам, в преодолении сложностей налогового сезона, ведения бухгалтерского учета и управления денежными потоками с уверенностью. Нажмите на ссылку в нашем профиле, чтобы воспользоваться скидкой прямо сейчас. Давайте заставим ваши финансы работать так же усердно, как и вы.


### #32 · a vegan burger bar (post)

**English:** Craving something bold yet plant-based? GreenBite is thrilled to unveil our newest creation this week: The Truffle Mushroom Smash. Packed with umami-rich flavors and topped with aged parmesan-style shavings, this burger is a game-changer. Join us every Thursday starting October 12th to taste the magic. Whether you're a lifelong vegan or just curious, this bite delivers pure satisfaction without compromise. Come hungry and leave happy! Don't miss out on this limited-time launch event. #VeganEats #PlantBased

**hymt2-7b** (MetricX 3.609): Хотите чего-то яркого и в то же время растительного? GreenBite с радостью представляет нашу самую новую разработку на этой неделе: The Truffle Mushroom Smash. Этот бургер, наполненный насыщенными вкусами умами и украшенный тонкими ломтиками сыра в стиле пармезан, полностью меняет правила игры. Присоединяйтесь к нам каждый четверг начиная с 12 октября, чтобы оценить всю красоту этого блюда. Независимо от того, являетесь ли вы сторонником веганства всю жизнь или просто проявляете интерес, этот бургер обеспечит вам истинное удовлетворение без каких-либо компромиссов. Приходите с голоду — уйдете довольными! Не пропустите это мероприятие, проводимое ограниченное время. #VeganEats #PlantBased

**qwen** (MetricX 5.103): Хочется чего-то яркого, но растительного? GreenBite с радостью представляет наш новый хит этой недели: бургер Truffle Mushroom Smash. Насыщенный вкус умами и стружка выдержанного пармезана делают его настоящим прорывом. Приходите к нам каждый четверг, начиная с 12 октября, чтобы оценить магию вкуса. Будь вы веганом всю жизнь или просто любите экспериментировать, этот бургер подарит чистое удовольствие без компромиссов. Приходите голодными, уходите счастливыми! Не пропустите это ограниченное по времени событие. #VeganEats #PlantBased

**qwen-think** (MetricX 9.74): Хочется чего-то яркого, но растительного? GreenBite с радостью представляет наш новый хит этой недели: бургер Truffle Mushroom Smash. Насыщенный вкус умами и стружка пармезана-альтернативы делают этот бургер настоящим прорывом. Приходите к нам каждый четверг, начиная с 12 октября, чтобы оценить магию вкуса. Будь вы веганом со стажем или просто любопытствующим, этот кусочек подарит чистое удовлетворение без компромиссов. Приходите голодными, уходите счастливыми! Не пропустите этот ограниченный по времени запуск. #VeganEats #PlantBased


### #40 · a gym with a 30-day trial (post)

**English:** Ever wonder what happens before the doors open at FlexFlow? Step into the early morning hustle where our staff meticulously sanitizes every machine and preps the studio for your workout. This behind-the-scenes glimpse reveals the dedication that goes into every session. Whether you're a beginner or a pro, our 30-day trial lets you experience this energy firsthand. Join us on Monday to start your journey with no pressure, just results. We are here to support your fitness goals every step of the way.

**hymt2-7b** (MetricX 1.456): Вы когда-нибудь задумывались о том, что происходит до открытия дверей в FlexFlow? Погрузитесь в утреннюю суету, когда наш персонал тщательно дезинфицирует каждый тренажер и готовит зал к вашим тренировкам. Этот взгляд за кулисами показывает старание, которое вкладывается в каждую сессию. Независимо от того, являетесь ли вы новичком или профессионалом, наша 30-дневная пробная версия позволит вам лично ощутить эту атмосферу энергии. Присоединяйтесь к нам в понедельник, чтобы начать свой путь без какого-либо давления — только реальные результаты. Мы всегда готовы поддерживать вас на каждом шагу в достижении ваших целей по фитнесу.

**qwen** (MetricX 3.658): Когда в FlexFlow начинается настоящая магия? Загляните за кулисы: пока город ещё спит, наша команда с педантичной точностью дезинфицирует каждый тренажёр и готовит зал к вашим тренировкам. За этим кадром скрывается безупречная забота о каждой детали. Попробуйте эту атмосферу на себе — с нашей 30-дневной пробной программой вы сможете оценить всё сами. Приходите в понедельник, чтобы начать свой путь без лишнего давления и с фокусом на результат. Мы рядом, чтобы поддержать вас на каждом этапе.

**qwen-think** (MetricX 3.637): Когда в FlexFlow начинается настоящая магия? Загляните за кулисы: пока город ещё спит, наша команда уже тщательно дезинфицирует каждый тренажёр и готовит зал к вашим тренировкам. За этим спокойствием стоит безупречная дисциплина. Попробуйте эту атмосферу на себе: 30-дневный пробный период даст вам полное представление о том, что нас ждёт. Присоединяйтесь к нам в понедельник, чтобы начать свой путь без лишнего давления — только реальные результаты. Мы поддержим вас на каждом этапе.


### #48 · a veterinary clinic (post)

**English:** Meet your pet’s new best friend! 🐾 PawTrust is offering a FREE consultation for all first-time customers. Whether you have a curious kitten or a playful puppy, our expert team is here to answer your questions and ensure a smooth start. This special offer is valid only on Saturday, November 18th. Don’t miss out on this chance to build a relationship with a clinic that truly cares. Book your slot today and give your furry family member the gentle care they deserve. See you there! 🐶🐱

**hymt2-7b** (MetricX 1.217): Познакомьтесь с новым лучшим другом вашего питомца! 🐾 PawTrust предлагает БЕСПЛАТНУЮ консультацию всем первым клиентам. Независимо от того, у вас любопытный котенок или игривый щенок, наша команда экспертов готова ответить на ваши вопросы и помочь начать процесс без проблем. Это специальное предложение действительно только в субботу, 18 ноября. Не упустите шанс наладить отношения с клиникой, которая действительно заботится о ваших питомцах. Забронируйте время уже сегодня и обеспечьте своему пушистому члену семьи ту заботу, которая ему необходима. Увидимся там! 🐶🐱

**qwen** (MetricX 1.843): Познакомьтесь с новым лучшим другом вашего питомца! 🐾 PawTrust дарит БЕСПЛАТНУЮ консультацию для всех новых клиентов. Есть ли у вас любопытный котёнок или игривый щенок? Наша команда экспертов готова ответить на все ваши вопросы и обеспечить комфортное начало. Это специальное предложение действует только в субботу, 18 ноября. Не упустите шанс наладить отношения с клиникой, которой действительно не всё равно. Запишитесь прямо сейчас и подарите своему пушистому члену семьи ту заботу, которую он заслуживает. Ждём вас! 🐶🐱

**qwen-think** (MetricX 1.835): Познакомьтесь с новым лучшим другом вашего питомца! 🐾 PawTrust дарит БЕСПЛАТНУЮ консультацию для всех новых клиентов. Есть ли у вас любопытный котёнок или игривый щенок? Наша команда экспертов готова ответить на все ваши вопросы и обеспечить комфортное начало. Это специальное предложение действует только в субботу, 18 ноября. Не упустите шанс наладить отношения с клиникой, которой действительно не всё равно. Запишитесь на приём уже сегодня и подарите своему пушистому члену семьи ту заботу, которую он заслуживает. Ждём вас! 🐶🐱


### #56 · a wedding photographer (post)

**English:** We are thrilled to introduce our latest offering: the Golden Hour Glow Edit Package, available exclusively this week! Capture the magic of sunset with enhanced warmth and softness in every frame. This special launch discount is valid until Sunday, October 29th. Don't miss your chance to elevate your wedding album with a touch of timeless elegance. Book your session today and let us freeze those fleeting moments in perfect light. #WeddingPhotography

**hymt2-7b** (MetricX 4.849): Мы с большой радостью представляем нашу самую новую услугу — пакет обработки Golden Hour Glow Edit, доступный исключительно на этой неделе! Зафиксируйте волшебство заката с усиленным теплом и мягкостью в каждом кадре. Эта специальная скидка при запуске действует до воскресенья, 29 октября. Не упустите шанс превратить ваш альбом свадьбы в произведение искусства с оттенком вечной элегантности. Забронируйте сеанс уже сегодня, и мы поможем сохранить эти мимолетные моменты в идеальном свете. #WeddingPhotography

**qwen** (MetricX 4.65): С радостью представляем новинку: пакет «Сияние золотого часа», доступный только на этой неделе! Запечатлите магию заката с усиленным теплом и мягкостью в каждом кадре. Специальная цена действует до воскресенья, 29 октября. Не упустите шанс придать альбому безупречную элегантность. Запишитесь на съемку прямо сейчас, чтобы сохранить эти мимолетные моменты в идеальном свете. #СвадебнаяФотография

**qwen-think** (MetricX 4.376): С радостью представляем нашу новинку: пакет «Сияние золотого часа», доступный только на этой неделе! Запечатлите магию заката с усиленным теплом и мягкостью в каждом кадре. Специальное предложение действует до воскресенья, 29 октября. Не упустите шанс придать вашему свадебному альбому оттенок вечной элегантности. Запишитесь на съемку прямо сейчас, и мы сохраним эти мимолетные мгновения в идеальном свете. #СвадебныйФотограф


### #64 · a furniture workshop making oak tables (post)

**English:** This week, we are thrilled to unveil our latest masterpiece: the 'Willow' dining table. Crafted from sustainably sourced white oak, this piece embodies timeless elegance and enduring strength. Each table is hand-finished by our skilled artisans, ensuring every grain tells a unique story. Perfect for family gatherings or intimate dinners, the Willow table brings warmth to your home. Join us this Saturday, October 21, for a special launch event where you can experience the craftsmanship firsthand. We can't wait to share this new addition to our collection with you. Visit our workshop or browse online to reserve yours before they are gone!

**hymt2-7b** (MetricX 2.267): На этой неделе мы с большой радостью представляем наше самое новое шедевральное творение — обеденный стол «Willow». Изготовленный из белого дуба, полученного экологически ответственным способом, этот предмет мебели воплощает в себе вечную элегантность и непоколебимую прочность. Каждый стол вручную дорабатывают наши искусные мастера, благодаря чему каждая волокнистая структура рассказывает свою уникальную историю. Идеально подходящий для семейных собраний или интимных ужинов, стол Willow придает тепла вашему дому. Присоединяйтесь к нам в эту субботу, 21 октября, на специальное мероприятие по запуску продукта, где вы сможете лично оценить мастерство производства. Мы с нетерпением ждем возможности поделиться с вами этим новым элементом нашей коллекции. Посетите нашу мастерскую или ознакомьтесь с ассортиментом онлайн, чтобы забронировать свой экземпляр до того, как он исчезнет!

**qwen** (MetricX 3.306): На этой неделе мы с радостью представляем вам нашу новую работу — обеденный стол «Вяз». Изготовленный из белого дуба, добытого устойчивыми методами, он сочетает в себе вневременную элегантность и надёжность. Каждый стол вручную обрабатывают наши мастера, поэтому узор древесины на каждом изделии уникален. Стол «Вяз» станет идеальным выбором для семейных ужинов и добавит уюта вашему дому. Приглашаем вас в эту субботу, 21 октября, на специальную презентацию, где вы сможете лично оценить качество исполнения. Мы не можем дождаться, когда вы полюбите эту новинку нашей коллекции. Посетите нашу мастерскую или оформите заказ онлайн, чтобы успеть забронировать стол, пока они есть в наличии!

**qwen-think** (MetricX 4.03): На этой неделе мы с радостью представляем наш новый шедевр — обеденный стол «Вербена». Изготовленный из экологически чистого белого дуба, он сочетает в себе вневременную элегантность и непреклонную прочность. Каждый стол вручную обрабатывается нашими мастерами, благодаря чему узор древесины рассказывает свою уникальную историю. Стол «Вербена» идеально подходит как для семейных ужинов, так и для уютных вечеров, привнося тепло в ваш дом. Приглашаем вас в эту субботу, 21 октября, на специальную презентацию, где вы сможете своими глазами оценить качество craftsmanship. Мы не можем дождаться, чтобы поделиться с вами этим новым дополнением нашей коллекции. Посетите нашу мастерскую или закажите стол онлайн, пока они есть в наличии!


### #72 · a children's clothing brand (post)

**English:** Hi friends! As the holidays wrap up, we want to make sure you can still find those perfect gifts for your little ones. TinyTrove will be closed on December 25th for a well-deserved rest with our families. We reopen our doors bright and early on December 26th to help you finish your holiday shopping. Check our story for any special weekend promotions! Let’s make the rest of the season sparkle.

**hymt2-7b** (MetricX 2.872): Привет, друзья! По мере того как праздники подходят к концу, мы хотим убедиться, что вы всё ещё сможете найти идеальные подарки для ваших малышей. TinyTrove будет закрыт 25 декабря, чтобы все смогли заслуженно отдохнуть с семьями. Мы снова откроемся рано утром 26 декабря, чтобы помочь вам завершить покупки к праздникам. Посмотрите наше видео, чтобы узнать о специальных акциях на выходных! Давайте сделаем оставшуюся часть сезона по-настоящему яркой.

**qwen** (MetricX 3.612): Привет, друзья! Пока праздники подходят к концу, мы хотим убедиться, что вы всё ещё сможете найти идеальные подарки для своих малышей. TinyTrove будет закрыто 25 декабря, чтобы мы могли отдохнуть и провести время с семьями. 26 декабря мы снова откроем двери, чтобы помочь вам завершить праздничные покупки. Следите за нашими историями — там вас ждут специальные предложения выходного дня! Пусть оставшаяся часть сезона будет волшебной.

**qwen-think** (MetricX 3.048): Привет, друзья! Пока праздники подходят к концу, мы хотим убедиться, что вы всё ещё сможете найти идеальные подарки для своих малышей. TinyTrove будет закрыто 25 декабря, чтобы мы могли отдохнуть и провести время с семьями. 26 декабря мы снова откроем двери, чтобы помочь вам завершить праздничные покупки. Загляните в наши сторис, чтобы узнать о специальных предложениях выходных! Давайте сделаем оставшуюся часть сезона яркой.


### #80 · a yoga studio with morning classes (post)

**English:** Meet Sarah, a busy mom who found her sanctuary at ZenithFlow. Every Tuesday morning at 7 AM, she trades the chaos for calm. Her journey from stressed to centered inspires us daily. Join Sarah and our community to find your own balance. We can't wait to welcome you to the mat. 🧘‍♀️✨ #yogalife

**hymt2-7b** (MetricX 1.69): Познакомьтесь с Сарой — занятой мамой, которая нашла свой уголок покоя в ZenithFlow. Каждое вторникое утро в 7 часов она меняет хаос на спокойствие. Ее путь от стресса к внутреннему равновесию вдохновляет нас каждый день. Присоединяйтесь к Саре и нашей комьюнити, чтобы найти собственное равновесие. Мы с нетерпением ждем, когда вы присоединитесь к нам на занятиях. 🧘‍♀️✨ #yogalife

**qwen** (MetricX 3.186): Познакомьтесь с Сарой, занятой мамой, которая нашла своё убежище в ZenithFlow. Каждое вторник в 7 утра она меняет хаос на спокойствие. Её путь от стресса к внутреннему равновесию вдохновляет нас каждый день. Присоединяйтесь к Саре и нашему сообществу, чтобы найти свой баланс. Мы с нетерпением ждём, когда примем вас на коврике. 🧘‍♀️✨ #yogalife

**qwen-think** (MetricX 3.186): Познакомьтесь с Сарой, занятой мамой, которая нашла своё убежище в ZenithFlow. Каждое вторник утром в 7:00 она меняет хаос на спокойствие. Её путь от стресса к внутреннему равновесию вдохновляет нас каждый день. Присоединяйтесь к Саре и нашему сообществу, чтобы найти свой баланс. Мы с нетерпением ждём, когда примем вас на коврике. 🧘‍♀️✨ #yogalife


### #88 · an online English tutor (post)

**English:** Ready to speak English with confidence? Join LingoLeap for a free 15-minute consultation on Monday! We’ll assess your current level and create a personalized learning plan tailored just for you. Whether you’re preparing for an exam or just want to chat more easily, our expert tutors are here to help. Don’t wait—spots are limited! Book your session today and take the first step toward fluency. Let’s make your language goals a reality together. Click the link in our bio to reserve your free slot now!

**hymt2-7b** (MetricX 0.992): Готовы говорить по-английски с уверенностью? Присоединяйтесь к LingoLeap для бесплатной 15-минутной консультации в понедельник! Мы оценим ваш нынешний уровень и разработаем индивидуальный план обучения, адаптированный именно для вас. Независимо от того, готовитесь ли вы к экзамену или просто хотите легче общаться, наши опытные преподаватели всегда готовы помочь. Не медлите — мест всего несколько! Забронируйте свою сессию сегодня и сделайте первый шаг к свободному владению языком. Давайте вместе сделаем ваши языковые цели реальностью. Нажмите на ссылку в нашем био, чтобы зарезервировать свое бесплатное время прямо сейчас!

**qwen** (MetricX 0.709): Готовы уверенно говорить по-английски? Присоединяйтесь к LingoLeap и запишитесь на бесплатную 15-минутную консультацию в понедельник! Мы оценим ваш текущий уровень и составим индивидуальный план обучения, разработанный специально для вас. Готовитесь к экзамену или просто хотите легче общаться — наши опытные преподаватели всегда готовы помочь. Не откладывайте, мест ограничено! Забронируйте занятие уже сегодня и сделайте первый шаг к свободному владению языком. Давайте вместе воплотим ваши языковые цели в жизнь. Переходите по ссылке в профиле, чтобы зарезервировать бесплатное место прямо сейчас!

**qwen-think** (MetricX 0.729): Готовы уверенно говорить по-английски? Присоединяйтесь к LingoLeap и запишитесь на бесплатную 15-минутную консультацию в понедельник! Мы оценим ваш текущий уровень и составим индивидуальный план обучения, разработанный специально для вас. Готовитесь к экзамену или просто хотите легче общаться — наши опытные преподаватели всегда готовы помочь. Не откладывайте, мест ограничено! Забронируйте занятие уже сегодня и сделайте первый шаг к свободному владению языком. Давайте вместе воплотим ваши языковые цели в жизнь. Переходите по ссылке в профиле, чтобы зарезервировать бесплатное время прямо сейчас!

## Ukrainian

### #0 · a Kyiv bakery selling sourdough and cinnamon rolls (post)

**English:** Olga brought her daughter to Kyiv Crumb for the first time last Tuesday, October 24th. She was nervous about the gluten-free options, but our staff guided her with care. As they sat by the window, the aroma of fresh sourdough and warm cinnamon rolls filled the air. Her daughter’s eyes lit up at the first bite, and Olga smiled with genuine relief. Moments like these remind us why we bake with heart. We are honored to be part of your city's daily rituals. Come taste the love in every crust and swirl.

**hymt2-7b** (MetricX 3.457): Минулого вівторка, 24 жовтня, Ольга вперше привела свою доньку до Kyiv Crumb. Вона хвилювалася щодо безглютенових страв, але наш персонал уважно її підтримав. Коли вони сиділи біля вікна, повітря наповнилося ароматом свіжого сирого хліба та теплих цедрових рулетів. Очі її доньки засяяли вже після першого кусочка, а Ольга з полегшенням посміхнулася. Саме такі моменти нагадують нам, чому ми печемо з серцем. Для нас велика честь бути частиною щоденних ритуалів вашого міста. Приходьте та відчуйте любов у кожній скоринці та вигині.

**qwen** (MetricX 2.422): Ольга вперше привела доньку до Kyiv Crumb у вівторок, 24 жовтня. Вона хвилювалася щодо безглютенових опцій, але наш персонал підтримав її з турботою. Сидячи біля вікна, вони насолоджувалися ароматом свіжого хліба на заквасці та теплих булочок з корицею. Очі доньки засвітилися від першого шматочка, а Ольга посміхнулася з щирим полегшенням. Саме такі моменти нагадують нам, чому ми випікаємо з душею. Ми щасливі бути частиною щоденних ритуалів вашого міста. Заходьте, щоб відчути любов у кожній скоринці та кожному завитку.

**qwen-think** (MetricX 2.415): У вівторок, 24 жовтня, Ольга вперше привела доньку до Kyiv Crumb. Вона хвилювалася щодо наявності безглютенових опцій, але наш персонал підтримав її з турботою. Сидячи біля вікна, вони насолоджувалися ароматом свіжого закваскового хліба та теплих булочок з корицею. Очі доньки засвітилися від першого шматочка, а Ольга посміхнулася з щирим полегшенням. Саме такі моменти нагадують нам, чому ми випікаємо з душею. Ми щасливі бути частиною щоденних ритуалів вашого міста. Заходьте, щоб відчути любов у кожній скоринці та кожному завитку.


### #8 · a mobile phone repair shop (post)

**English:** Cracked screen? Don't panic! At SnapFix, we’re offering a free consultation for first-time customers. Bring your device in before October 15th and let our experts assess the damage with zero obligation. We’ll give you an honest quote and timeline so you know exactly what to expect. No hidden fees, no pressure—just quick, reliable repairs. Stop by today and let’s get your phone back to life. #SnapFix

**hymt2-7b** (MetricX 1.252): Розбите екран? Не панікуйте! У SnapFix ми пропонуємо безкоштовну консультацію для клієнтів, які звертаються до нас уперше. Принесіть свій пристрій до нас до 15 жовтня, і наші фахівці оцінять пошкодження без будь-яких зобов’язань з вашого боку. Ми надамо вам чесну ціну та приблизний термін виконання робіт, щоб ви точно знали, чого очікувати. Жодних прихованих оплат, жодного тиску — лише швидкі та надійні ремонти. Завітайте сьогодні, і ми допоможемо повернути ваш телефон до життя. #SnapFix

**qwen** (MetricX 2.08): Трісклий екран? Не панікуйте! У SnapFix ми пропонуємо безкоштовну консультацію для нових клієнтів. Привезіть свій пристрій до 15 жовтня, і наші експерти оцінять пошкодження без будь-яких зобов'язань. Ми надамо чесну ціну та терміни, щоб ви точно знали, чого чекати. Жодних прихованих платежів, жодного тиску — лише швидкий та надійний ремонт. Заходьте сьогодні, і ми повернемо вашому телефону життя. #SnapFix

**qwen-think** (MetricX 2.002): Трісклий екран? Не панікуйте! У SnapFix ми пропонуємо безкоштовну консультацію для нових клієнтів. Привезіть свій пристрій до 15 жовтня, і наші фахівці оцінять пошкодження без будь-яких зобов’язань. Ми надамо чесну ціну та терміни, щоб ви точно знали, чого чекати. Жодних прихованих платежів, жодного тиску — лише швидкий та надійний ремонт. Заходьте сьогодні, і ми повернемо вашому телефону життя. #SnapFix


### #16 · a nail salon (post)

**English:** Ever wondered what happens between your appointment? Here is a behind-the-scenes peek at our daily routine! We meticulously sanitize every tool and station to ensure your safety and comfort. Our team prepares fresh supplies and reviews hygiene protocols before opening doors at 9 AM on Monday. This dedication to cleanliness is what makes LuxeNails your trusted spot for flawless manicures and pedicures. We believe that great nails start with a sterile, professional environment. Come see the care we put into every detail. Book your next appointment today and experience the difference! #NailSalon

**hymt2-7b** (MetricX 2.362): Чи цікавило вас коли-небудь, що відбувається під час вашого прийому? Ось зазирання за куліси у нашу щоденну рутину! Ми ретельно дезінфікуємо кожен інструмент та рабоче місце, щоб забезпечити вашу безпеку та комфорт. Наша команда готує свіжі матеріали та перевіряє протоколи гігієни перед тим, як о 9 ранку у понеділок відкрити двері. Саме така відданість чистоті робить LuxeNails вашим надійним місцем для ідеальних манікюрів та педикюрів. Ми віримо, що чудові нігті починаються з стерильного, професійного середовища. Приходьте та побачте, яку увагу ми приділяємо кожній деталі. Забронюйте свій наступний прийом вже сьогодні та відчуйте різницю! #NailSalon

**qwen** (MetricX 2.181): Колись цікавилися, що ж відбувається між вашими візитами? Загляньте за лаштунки нашого повсякденного життя! Ми ретельно дезінфікуємо кожен інструмент і робоче місце, щоб забезпечити вашу безпеку та комфорт. Наша команда готує свіжі матеріали та перевіряє протоколи гігієни перед відкриттям о 9:00 у понеділок. Саме така увага до чистоти робить LuxeNails вашим надійним місцем для бездоганного манікюру та педикюру. Ми переконані: ідеальні нігті починаються зі стерильного та професійного середовища. Переконайтеся в турботі, яку ми вкладаємо в кожну деталь. Запишіться на наступну процедуру вже сьогодні та відчуйте різницю! #NailSalon

**qwen-think** (MetricX 3.115): Коли-небудь цікавилися, що відбувається між вашими візитами? Ось погляд за лаштунки нашого щоденного життя! Ми ретельно дезінфікуємо кожен інструмент і робоче місце, щоб забезпечити вашу безпеку та комфорт. Наша команда готує свіжі матеріали та перевіряє протоколи гігієни перед відкриттям о 9:00 у понеділок. Саме ця відданість чистоті робить LuxeNails вашим надійним місцем для бездоганних манікюрів та педикюрів. Ми віримо, що ідеальні нігті починаються зі стерильного та професійного середовища. Приходьте й переконайтеся в турботі, яку ми вкладання в кожну деталь. Запишіться на наступну процедуру вже сьогодні та відчуйте різницю! #NailSalon


### #24 · an accounting firm for small businesses (post)

**English:** Ready to scale your small business without the financial stress? LedgerLeap is here to simplify your numbers so you can focus on growth. To celebrate our launch, we’re offering an exclusive 20% discount on our foundational accounting packages. This special offer is only available until Friday, so don't wait to secure your spot. Our team specializes in helping entrepreneurs like you navigate tax season, bookkeeping, and cash flow management with confidence. Click the link in our bio to claim your discount today. Let’s make your finances work as hard as you do.

**hymt2-7b** (MetricX 1.672): Готові розширювати свій маленький бізнес без фінансового тиску? LedgerLeap допоможе спростити обробку ваших фінансових даних, щоб ви могли зосередитися на розвитку. На честь запуску ми пропонуємо ексклюзивну знижку 20% на наші базові пакети бухгалтерського обліку. Ця спеціальна пропозиція діє лише до п’ятниці, тому не зволікайте — забронюйте своє місце вже зараз. Наша команда спеціалізується на допомозі підприємцям, схожим на вас, у впевненому проходженні податкового сезону, веденні бухгалтерського обліку та управлінні грошовими потоками. Натисніть посилання у нашому біо, щоб отримати знижку вже сьогодні. Давайте зробимо так, щоб ваші фінанси працювали так само наполегливо, як і ви.

**qwen** (MetricX 5.791): Готові масштабувати свій малий бізнес без фінансової напруги? LedgerLeap спрощує ваші цифри, щоб ви могли зосередитися на зростанні. На честь запуску ми пропонуємо ексклюзивну знижку 20% на наші базові пакети бухгалтерських послуг. Ця спеціальна пропозиція діє лише до п’ятниці, тому не зволікайте, щоб зарезервувати своє місце. Наша команда спеціалізується на допомозі підприємцям, як-от ви, впевнено проходити податковий сезон, вести облік та керувати грошовими потоками. Натисніть посилання в нашому профілі, щоб скористатися знижкою вже сьогодні. Нехай ваші фінанси працюють так само наполегливо, як і ви.

**qwen-think** (MetricX 4.518): Готові масштабувати свій малий бізнес без фінансової напруги? LedgerLeap спрощує вашу звітність, щоб ви могли зосередитися на зростанні. На честь запуску ми даруємо ексклюзивну знижку 20% на наші базові пакети бухгалтерських послуг. Ця пропозиція діє лише до п’ятниці, тому не зволікайте, щоб зарезервувати своє місце. Наша команда спеціалізується на тому, щоб допомогти підприємцям, як ви, впевнено проходити податковий сезон, вести облік та керувати грошовими потоками. Натисніть посилання в нашому профілі, щоб скористатися знижкою вже сьогодні. Нехай ваші фінанси працюють так само наполегливо, як і ви.


### #32 · a vegan burger bar (post)

**English:** Craving something bold yet plant-based? GreenBite is thrilled to unveil our newest creation this week: The Truffle Mushroom Smash. Packed with umami-rich flavors and topped with aged parmesan-style shavings, this burger is a game-changer. Join us every Thursday starting October 12th to taste the magic. Whether you're a lifelong vegan or just curious, this bite delivers pure satisfaction without compromise. Come hungry and leave happy! Don't miss out on this limited-time launch event. #VeganEats #PlantBased

**hymt2-7b** (MetricX 4.921): Хочете щось незвичайне, але рослинного походження? GreenBite з радістю представляє нашу найновішу створіння цього тижня: The Truffle Mushroom Smash. Цей бургер наповнений насиченими смаками умамі та прикрашений тонкими шматочками сиру у стилі пармезан, що робить його справжнім проривом. Приєднуйтесь до нас щочетверга, починаючи з 12 жовтня, щоб відчути цю магію. Незалежно від того, чи ви веган з дитинства чи просто цікавитеся, цей бургер принесе вам справжнє задоволення без жодних компромісів. Приходьте голодними та йдіть щасливими! Не пропустіть цю подію, яка триватиме лише обмежений час. #VeganEats #PlantBased

**qwen** (MetricX 6.871): Хочеться чогось яскравого та рослинного? GreenBite з радістю презентує нашу новинку цього тижня: Truffle Mushroom Smash. Насичений смаком умами та прикрашений стружкою з витриманого пармезану, цей бургер справді змінює правила гри. Запрошуємо вас у кожен четвер, починаючи з 12 жовтня, щоб спробувати магію на смак. Чи ви веган з дитинства, чи просто цікавитеся рослинною їжею — цей шматочок подарує чисте задоволення без компромісів. Приходьте з апетитом і йдіть щасливими! Не пропустіть цю обмежену запускову подію. #VeganEats #PlantBased

**qwen-think** (MetricX 8.465): Хочеться чогось яскравого та рослинного? GreenBite з радістю представляє нашу новинку цього тижня: Truffle Mushroom Smash. Насичений смаком умами та прикрашений пластівцями витриманого пармезану, цей бургер змінює правила гри. Запрошуємо вас у кожен четвер, починаючи з 12 жовтня, щоб спробувати магію смаку. Чи ви веган з досвіду, чи просто цікавитеся — цей шматочок дарує чисте задоволення без компромісів. Приходьте з апетитом і йдіть щасливими! Не пропустіть цю обмежену запускну подію. #VeganEats #PlantBased


### #40 · a gym with a 30-day trial (post)

**English:** Ever wonder what happens before the doors open at FlexFlow? Step into the early morning hustle where our staff meticulously sanitizes every machine and preps the studio for your workout. This behind-the-scenes glimpse reveals the dedication that goes into every session. Whether you're a beginner or a pro, our 30-day trial lets you experience this energy firsthand. Join us on Monday to start your journey with no pressure, just results. We are here to support your fitness goals every step of the way.

**hymt2-7b** (MetricX 1.834): Чи цікавило вас коли-небудь, що відбувається перед тим, як відчиняються двері у FlexFlow? Увійдіть у атмосферу ранкової метушні, де наш персонал ретельно дезінфікує кожен тренажер та підготовлює зал до вашого тренування. Цей знайомство з процесом позаду кадрів показує старанність, яку ми докладаємо до кожної сесії. Незалежно від того, чи є ви початківцем, чи професіоналом, наша 30-денна пробна версія дозволяє вам особисто відчути цю енергію. Приєднуйтесь до нас у понеділок, щоб розпочати свою подорож без будь-якого тиску — лише з результатами. Ми завжди тут, щоб підтримувати вас у досягненні ваших фізичних цілей на кожному кроці.

**qwen** (MetricX 1.895): Коли-небудь цікавилися, що відбувається в FlexFlow до відкриття дверей? Пориньте в ранкову метушню, де наша команда ретельно дезінфікує кожний тренажер і готує зал до ваших тренувань. Цей погляд «за лаштунками» розкриває відданість, яка вкладена в кожну сесію. Незалежно від того, чи ви новачок, чи професіонал, наш 30-денний пробний період дозволить вам відчути цю енергію на собі. Приєднуйтесь до нас у понеділок, щоб розпочати свій шлях без зайвого тиску — лише результати. Ми підтримуємо ваші фітнес-цілі на кожному кроці.

**qwen-think** (MetricX 2.218): Коли-небудь цікавилися, що відбувається до відкриття дверей у FlexFlow? Пориньте в ранкову метушню, де наша команда ретельно дезінфікує кожний тренажер та готує студію до вашої тренування. Цей погляд за лаштунки розкриває відданість, яка вкладена в кожне заняття. Незалежно від того, чи ви новачок, чи професіонал, наш 30-денний пробний період дозволяє відчути цю енергію на собі. Приєднуйтесь до нас у понеділок, щоб розпочати свій шлях без зайвого тиску, лише з результатами. Ми підтримуємо ваші фітнес-цілі на кожному кроці.


### #48 · a veterinary clinic (post)

**English:** Meet your pet’s new best friend! 🐾 PawTrust is offering a FREE consultation for all first-time customers. Whether you have a curious kitten or a playful puppy, our expert team is here to answer your questions and ensure a smooth start. This special offer is valid only on Saturday, November 18th. Don’t miss out on this chance to build a relationship with a clinic that truly cares. Book your slot today and give your furry family member the gentle care they deserve. See you there! 🐶🐱

**hymt2-7b** (MetricX 2.719): Познайомтеся з новим найкращим другом вашого улюбленця! 🐾 PawTrust пропонує БЕЗКОШТОВну консультацію для всіх клієнтів, які приходять уперше. Незалежно від того, чи це допитлива кішка, чи веселий цуценя, наша команда експертів готова відповісти на ваші запитання та забезпечити безпроблемний початок співпраці. Ця спеціальна пропозиція діє лише у суботу, 18 листопада. Не пропустіть можливість налагодити стосунки з клінікою, яка справді піклується про ваших тварин. Забронюйте свою дату ще сьогодні та надайте своєму шерстяному члену родини ту турботу, якої він заслуговує. Бачимося там! 🐶🐱

**qwen** (MetricX 3.152): Познайомтеся з новим найкращим другом вашого улюбленця! 🐾 PawTrust пропонує БЕЗКОШТОВНУ консультацію для всіх нових клієнтів. Чи у вас допитливий кошеня, чи грайливий цуценя — наша команда експертів готова відповісти на ваші запитання та забезпечити легке початок. Ця спеціальна пропозиція діє лише у суботу, 18 листопада. Не пропустіть шанс побудувати стосунки з клінікою, яка дійсно дбає. Запишіться на прийом сьогодні та подаруйте своєму хутряному члену родини ніжний догляд, якого він заслуговує. Побачимося! 🐶🐱

**qwen-think** (MetricX 1.868): Познайомтеся з новим найкращим другом вашого улюбленця! 🐾 PawTrust пропонує БЕЗКОШТОВНУ консультацію для всіх нових клієнтів. Чи у вас допитливий кошеня, чи грайливий цуценя — наша команда експертів готова відповісти на ваші запитання та забезпечити легкий старт. Ця спеціальна пропозиція діє лише у суботу, 18 листопада. Не пропустіть шанс побудувати стосунки з клінікою, якій дійсно не байдуже. Запишіться на прийом сьогодні та подаруйте своєму хутряному члену родини ніжний догляд, якого він заслуговує. Побачимось! 🐶🐱


### #56 · a wedding photographer (post)

**English:** We are thrilled to introduce our latest offering: the Golden Hour Glow Edit Package, available exclusively this week! Capture the magic of sunset with enhanced warmth and softness in every frame. This special launch discount is valid until Sunday, October 29th. Don't miss your chance to elevate your wedding album with a touch of timeless elegance. Book your session today and let us freeze those fleeting moments in perfect light. #WeddingPhotography

**hymt2-7b** (MetricX 4.513): Ми з великою радістю представляємо нашу найновішу пропозицію – пакет обробки Golden Hour Glow Edit, який доступний виключно цього тижня! Запишіть магію заходу сонця завдяки посиленій теплоті та м’якості кожного кадру. Ця спеціальна знижка на старт дійсна до неділі, 29 жовтня. Не упустіть можливість надати вашому весільному альбому відтінку вічної елегантності. Забронюйте свою сесію вже сьогодні та дозвольте нам зафіксувати ці миттєвості у ідеальному світлі. #WeddingPhotography

**qwen** (MetricX 4.828): Ми з радістю представляємо нашу новинку: пакет обробки Golden Hour Glow Edit, доступний виключно цього тижня! Захопіть магію заходу сонця з підкресленою теплотою та м’якістю в кожному кадрі. Ця спеціальна знижка на запуск діє до неділі, 29 жовтня. Не пропустіть шанс підняти рівень вашого весільного альбому, додавши йому дотику вічної елегантності. Запишіться на зйомку вже сьогодні, і ми збережемо ці крихкі моменти в ідеальному світлі. #WeddingPhotography

**qwen-think** (MetricX 4.953): Ми з радістю презентуємо нашу новинку: пакет обробки Golden Hour Glow Edit, доступний виключно цього тижня! Захопіть магію заходу сонця з підкресленою теплотою та м’якістю в кожному кадрі. Ця спеціальна пропозиція діє до неділі, 29 жовтня. Не пропустіть шанс підняти рівень вашого весільного альбому, додавши йому дотику вічної елегантності. Запишіться на зйомку вже сьогодні, і ми зупинимо ці крихкі моменти в ідеальному світлі. #WeddingPhotography


### #64 · a furniture workshop making oak tables (post)

**English:** This week, we are thrilled to unveil our latest masterpiece: the 'Willow' dining table. Crafted from sustainably sourced white oak, this piece embodies timeless elegance and enduring strength. Each table is hand-finished by our skilled artisans, ensuring every grain tells a unique story. Perfect for family gatherings or intimate dinners, the Willow table brings warmth to your home. Join us this Saturday, October 21, for a special launch event where you can experience the craftsmanship firsthand. We can't wait to share this new addition to our collection with you. Visit our workshop or browse online to reserve yours before they are gone!

**hymt2-7b** (MetricX 4.127): Цього тижня ми з великою радістю представляємо нашу найновішу шедевральну роботу – обідній стіл «Willow». Виготовлений із білого дуба, отриманого екологічно, цей виріб уособлює вічну елегантність та міцність. Кожен стіл ретельно доробляється нашими кваліфікованими майстрами, що гарантує, що кожна волокнина розповідає унікальну історію. Ідеальний для сімейних зустрічей чи інтимних вечерь, стіл Willow приносить тепло у ваш дім. Приєднуйтесь до нас у суботу, 21 жовтня, на спеціальну презентацію, де ви зможете особисто оцінити майстерність виконання. Ми з нетерпінням чекаємо, щоб поділитися цим новим елементом нашої колекції з вами. Відвідайте нашу майстерню або перегляньте товари онлайн, щоб забронювати свій екземпляр, поки вони ще є!

**qwen** (MetricX 4.091): Цього тижня ми з радістю презентуємо наш новий шедевр — обідній стіл «Вілла». Виготовлений з білого дуба, отриманого з відповідальних джерел, цей стіл поєднує вічну елегантність із надзвичайною міцністю. Кожен стіл ручної роботи, який наші майстри фінішують вручну, гарантує, що кожна текстура розповідає свою унікальну історію. Ідеальний варіант для сімейних зборів чи затишних вечерь, стіл «Вілла» дарує вашому дому тепло. Приєднуйтесь до нас у суботу, 21 жовтня, на спеціальну подію запуску, де ви зможете особисто оцінити якість майстерності. Ми не можемо дочекатися, щоб поділитися з вами цим новим доповненням нашої колекції. Відвідайте нашу майстерню або перегляньте каталог онлайн, щоб забронювати свій стіл, поки вони ще є в наявності!

**qwen-think** (MetricX 3.736): Цього тижня ми з радістю презентуємо наш новий шедевр — обідній стіл «Вілла». Виготовлений з білого дуба, отриманого з відповідальних джерел, цей виріб поєднує вічну елегантність із надзвичайною міцністю. Кожен стіл ручної роботи, майстри ретельно обробляють поверхню, щоб кожна текстура дерева розповідала свою унікальну історію. Ідеальний вибір для родинних зборів чи затишних вечерь, стіл «Вілла» дарує вашому дому тепло. Приєднуйтесь до нас у суботу, 21 жовтня, на спеціальну подію запуску, де ви зможете особисто оцінити якість виконання. Ми не можемо дочекатися, коли поділимося з вами цим новим доповненням нашої колекції. Відвідайте нашу майстерню або перегляньте каталог онлайн, щоб зарезервувати свій стіл, поки вони ще є в наявності!


### #72 · a children's clothing brand (post)

**English:** Hi friends! As the holidays wrap up, we want to make sure you can still find those perfect gifts for your little ones. TinyTrove will be closed on December 25th for a well-deserved rest with our families. We reopen our doors bright and early on December 26th to help you finish your holiday shopping. Check our story for any special weekend promotions! Let’s make the rest of the season sparkle.

**hymt2-7b** (MetricX 3.207): Привіт, друзі! Оскільки свята добігають кінця, ми хочемо переконатися, що ви все ще зможете знайти ідеальні подарунки для ваших малюків. TinyTrove буде закритий 25 грудня, щоб наші працівники могли належним чином відпочити з родинами. Ми знову відкриємося рано-вранці 26 грудня, щоб допомогти вам завершити покупки на свята. Перегляньте нашу сторіс, щоб дізнатися про будь-які спеціальні пропозиції на вихідних! Давайте зробимо решту сезону ще яскравішою.

**qwen** (MetricX 4.069): Привіт, друзі! Оскільки свята добігають кінця, ми хочемо переконатися, що ви зможете знайти ідеальні подарунки для своїх малюків. TinyTrove буде закрито 25 грудня, щоб ми могли відпочити та провести час із родиною. 26 грудня ми знову відкриваємо наші двері, щоб допомогти вам завершити святкові покупки. Перевірте наші історії, щоб дізнатися про спеціальні пропозиції вихідного дня! Нехай решта сезону сяє.

**qwen-think** (MetricX 3.959): Привіт, друзі! Оскільки свята добігають кінця, ми хочемо переконатися, що ви зможете знайти ідеальні подарунки для своїх малюків. TinyTrove буде закрито 25 грудня, щоб ми могли відпочити та провести час із родиною. Ми знову відкриємо наші двері 26 грудня, щоб допомогти вам завершити святкові покупки. Перевірте наші історії, щоб дізнатися про спеціальні пропозиції на вихідні! Нехай решта сезону сяє.


### #80 · a yoga studio with morning classes (post)

**English:** Meet Sarah, a busy mom who found her sanctuary at ZenithFlow. Every Tuesday morning at 7 AM, she trades the chaos for calm. Her journey from stressed to centered inspires us daily. Join Sarah and our community to find your own balance. We can't wait to welcome you to the mat. 🧘‍♀️✨ #yogalife

**hymt2-7b** (MetricX 1.446): Познайомтеся з Сарою — зайнятою мамою, яка знайшла свій притулок у ZenithFlow. Щоранку у вівторок о 7 годині ранку вона змінює хаос на спокій. Її шлях від стресу до внутрішньої рівноваги надихає нас щодня. Приєднуйтесь до Сари та нашої спільноти, щоб знайти власну рівновагу. Ми з нетерпінням чекаємо, коли зможемо запросити вас на заняття. 🧘‍♀️✨ #yogalife

**qwen** (MetricX 2.108): Познайомтеся з Сарою, зайнятою мамою, яка знайшла свій притулок у ZenithFlow. Щовівторка о 7:00 вона обирає спокій замість хаосу. Її шлях від стресу до внутрішньої рівноваги надихає нас щодня. Приєднуйтесь до Сари та нашої спільноти, щоб знайти власний баланс. Ми не можемо дочекатися, коли привітаємо вас на килимку. 🧘‍♀️✨ #yogalife

**qwen-think** (MetricX 1.985): Познайомтеся з Сарою, зайнятою мамою, яка знайшла свій притулок у ZenithFlow. Щовівторка о 7:00 вона обирає спокій замість хаосу. Її шлях від стресу до внутрішньої рівноваги надихає нас щодня. Долучайтеся до Сари та нашої спільноти, щоб знайти власний баланс. Ми не можемо дочекатися, щоб привітати вас на килимку. 🧘‍♀️✨ #yogalife


### #88 · an online English tutor (post)

**English:** Ready to speak English with confidence? Join LingoLeap for a free 15-minute consultation on Monday! We’ll assess your current level and create a personalized learning plan tailored just for you. Whether you’re preparing for an exam or just want to chat more easily, our expert tutors are here to help. Don’t wait—spots are limited! Book your session today and take the first step toward fluency. Let’s make your language goals a reality together. Click the link in our bio to reserve your free slot now!

**hymt2-7b** (MetricX 0.93): Готові говорити англійською з впевненістю? Приєднуйтесь до LingoLeap для безкоштовної 15-хвилинної консультації у понеділок! Ми оцінимо ваш поточний рівень та створимо індивідуальний план навчання, розроблений саме для вас. Незалежно від того, чи готуєтеся ви до іспиту, чи просто хочете легше спілкуватися, наші досвідчені викладачі завжди готові допомогти. Не зволікайте — місць обмежено! Забронюйте свою сесію вже сьогодні та зробіть перший крок до вільного володіння мовою. Давайте разом зробимо ваші мовні цілі реальністю. Натисніть на посилання у нашому біо, щоб негайно забронювати свою безкоштовну позицію!

**qwen** (MetricX 1.873): Готові впевнено говорити англійською? Приєднуйтесь до LingoLeap на безкоштовну 15-хвилинну консультацію у понеділок! Ми оцінимо ваш поточний рівень та створимо індивідуальний план навчання, адаптований саме для вас. Чи готуєтеся ви до іспиту, чи просто хочете легше спілкуватися — наші експертні репетитори завжди готові допомогти. Не зволікайте — кількість місць обмежена! Запишіться на сеанс сьогодні та зробіть перший крок до вільного володіння мовою. Разом ми зробимо ваші мовні цілі реальністю. Натисніть посилання в біо, щоб зарезервувати безкоштовний слот прямо зараз!

**qwen-think** (MetricX 0.778): Готові впевнено говорити англійською? Приєднуйтесь до LingoLeap на безкоштовну 15-хвилинну консультацію у понеділок! Ми оцінимо ваш поточний рівень та створимо індивідуальний план навчання, адаптований саме для вас. Чи готуєтесь ви до іспиту, чи просто хочете легше спілкуватися — наші експертні репетитори завжди готові допомогти. Не зволікайте — кількість місць обмежена! Запишіться на сеанс сьогодні та зробіть перший крок до вільного володіння мовою. Разом ми зробимо ваші мовні цілі реальністю. Натисніть посилання в нашому профілі, щоб забронювати безкоштовне місце вже зараз!

