/* The parent page. Vanilla JS, no build step, no framework — one page, one phone, four screens.
 *
 * Three things this file is careful about:
 *
 * 1. **The day turns over at 04:00, on the phone's clock** — the same rule the TV follows
 *    (docs/PARENTAL.md, 数据). It is computed from calendar fields, not by subtracting four hours from
 *    a timestamp: subtracting an instant moves the roll-over to 05:00 for the half of the year when
 *    daylight saving is in effect, which is exactly the bug the Kotlin side already had once.
 * 2. **Every string the server sends is escaped before it reaches innerHTML.** The question bank is
 *    ours, but "our own data" is how stored XSS usually starts.
 * 3. **A 401 always means "not paired"**, and goes back to the pairing screen instead of an error
 *    toast: a stale cookie is a state, not a failure.
 */

'use strict';

// ---------------------------------------------------------------- languages

const LANGS = ['zh', 'en', 'ru', 'uk', 'kk'];
const LOCALE = { zh: 'zh-CN', en: 'en-US', ru: 'ru-RU', uk: 'uk-UA', kk: 'kk-KZ' };
const CHIP = { zh: '中', en: 'EN', ru: 'РУ', uk: 'УК', kk: 'ҚА' };

const TEXTS = {
  zh: {
    app: { parent: '家长' },
    tab: { today: '今日', week: '周报', wrong: '错题本', settings: '设置' },
    unit: { h: '小时', m: '分钟', sep: ' ' },
    pair: {
      title: '连接电视',
      hint: '在电视上打开「设置 → 家长模式 → 手机配对」，把屏幕上的六位码输在这里。',
      code: '配对码',
      case: '六位码区分大小写，照抄电视上的样子。',
      submit: '连接',
      ttl: '码十分钟内有效；过期就在电视上重新生成一次。',
      wrong: '码不对。对着电视再看一遍——大小写也要一样。',
      expired: '码已经过期了，在电视上重新生成一次。',
      rate: '试得太频繁了，过几分钟再试。',
      revoked: '这台电视已经解绑了。',
      net: '连不上服务端，检查手机是不是连在家里的 Wi-Fi 上。',
    },
    auth: {
      signInTitle: '登录', signUpTitle: '注册账号', claimTitle: '升级成账号',
      signIn: '登录', signUp: '注册', claim: '完成升级',
      email: '邮箱', password: '密码', password2: '再输一次密码', newPassword: '新密码',
      questionLabel: '找回密码的问题', answerLabel: '问题的答案', oldPassword: '当前密码',
      toSignUp: '还没有账号？注册一个', toSignIn: '已经有账号了？去登录',
      hintIn: '用注册时的邮箱和密码登录。',
      hintUp: '填邮箱，设两次密码——这封邮箱收不到验证信。想留条后路，就在下面写一个只有你知道答案的问题。',
      questionGroup: '忘记密码时（选填）',
      hintClaim: '这台手机还连着「{name}」。设一个邮箱和密码，它就变成你的账号，电视不用重新取码。',
      badEmail: '邮箱格式不对。', shortPassword: '密码至少 8 位。',
      commonPassword: '这个密码太常见了，换一个。',
      taken: '这个邮箱已经注册过了，直接登录吧。', wrong: '邮箱或密码不对。',
      rate: '试得太频繁了，等一会儿再试。', locked: '输错次数太多，过 15 分钟再试。',
      retry: '这一步没走通，再点一次就好。',
      noReset: '没有邮件找回：忘记密码时，用你设的问题和答案直接重设。',
      ruleLength: '至少 8 位', ruleDigits: '不能只有数字',
      ruleNotEmail: '不能是邮箱的前半段', ruleCommon: '不要用 12345678 这类',
      ruleMatch: '两次输入一致',
      forgot: '忘记密码了？',
      recoverTitle: '找回密码', hintRecover: '先填注册时的邮箱，我们把你设的问题拿给你看。',
      hintRecover2: '答对问题就能直接设新密码。',
      askQuestion: '看我的问题', setPassword: '重设密码',
      questionIs: '你的问题：{question}',
      changeTitle: '修改密码',
      hintChange: '先输现在的密码，再输两次新密码。改完之后这台手机也要重新登录。',
      changeSubmit: '修改密码',
      mismatch: '两次输入的密码不一样。',
      needQuestion: '写一个只有你知道答案的问题。', needAnswer: '写下答案，忘记密码时就靠它。',
      badAnswer: '答案不对。', recoverLocked: '答错次数太多，过 15 分钟再试。',
      noAccount: '这个邮箱没有注册过。',
      noQuestion: '这个账号没有设问题，用不了这种方式。',
      samePassword: '这就是现在用的密码。', weakPassword: '密码不能只有数字。',
      changed: '密码已改，请用新密码登录。', resetDone: '密码已重设，用新密码登录。',
      setQuestionTitle: '找回问题', hintSetQuestion: '忘记密码时靠它回来。改掉之后，旧的答案就作废了。',
      saveQuestion: '保存', questionSaved: '已保存。',
      signOut: '退出登录', switch: '切换电视', addTv: '连接另一台电视',
      devices: '这个账号的电视', current: '正在看的',
    },
    today: {
      left: '今天还剩',
      unlimited: '今日不限时长',
      used: '今天已看', limit: '每日上限', earned: '做题换到',
      passed: '已通过', attempts: '今天做过', times: '{n} 次', canDo: '还能做',
      cooldown: '休息中', cooldownNote: '再等一会儿才能做下一组',
      noreport: '电视还没报数',
      noreportHint: '电视开着的时候会每半小时报一次。现在这个数字要等它开机。',
      hint: '数字来自电视，最多晚半小时；用密码加的时间不算在内。',
      extra: '不含密码临时加的时间',
    },
    settings: {
      daily: '每日观看时长', session: '单次观看时长', interval: '间隔',
      unlimited: '不限', minutes: '{n} 分钟', hours: '{h} 小时', hoursMinutes: '{h} 小时 {m} 分钟',
      saved: '已保存', delay: '电视开着的话，五分钟内会应用。',
      noteDaily: '设成「不限」就是这一天不封顶。',
      noteSession: '单次时长设成「不限」时，间隔不会触发。',
      noteInterval: '看满单次时长后要休息多久。',
      unpair: '解绑这台手机',
      unpairConfirm: '解绑后要重新在电视上取码。确定？',
      change: '修改密码', setQuestion: '找回问题',
      language: '语言',
    },
    wrong: {
      empty: '最近没有错题', emptyHint: '做错的题会出现在这里，带着正确答案和解析。',
      yours: '他答', right: '答案', days: '最近 {n} 天',
      flagged: '标记没学过',
    },
    week: {
      attempts: '做题', passed: '通过', rate: '通过率', earned: '换到',
      worst: '错得最多的知识点', none: '还没错过题',
      chart: '每天看了多久',
      legendUsed: '其余已看', legendEarned: '做题换的',
      empty: '这一周还没有记录',
      times: '{n} 次',
    },
    common: { retry: '重试', close: '关闭', loading: '加载中…', error: '出错了，稍后再试。' },
  },

  en: {
    app: { parent: 'Parent' },
    tab: { today: 'Today', week: 'Week', wrong: 'Mistakes', settings: 'Settings' },
    unit: { h: 'h', m: 'min', sep: ' ' },
    pair: {
      title: 'Connect the TV',
      hint: 'On the TV open Settings → Parental → Phone pairing, then type the six characters here.',
      code: 'Pairing code',
      case: 'The code is case-sensitive — copy it exactly as it looks.',
      submit: 'Connect',
      ttl: 'Valid for ten minutes. If it expires, ask the TV for a new one.',
      wrong: 'That code is not right. Check the TV again — capitals matter.',
      expired: 'The code has expired. Generate a new one on the TV.',
      rate: 'Too many tries. Wait a few minutes.',
      revoked: 'This TV has been unpaired.',
      net: 'Cannot reach the server. Is the phone on the home Wi-Fi?',
    },
    auth: {
      signInTitle: 'Sign in', signUpTitle: 'Create an account', claimTitle: 'Turn this phone into an account',
      signIn: 'Sign in', signUp: 'Create account', claim: 'Finish',
      email: 'Email', password: 'Password', password2: 'Password again', newPassword: 'New password',
      questionLabel: 'Recovery question', answerLabel: 'Answer', oldPassword: 'Current password',
      toSignUp: 'No account yet? Create one', toSignIn: 'Already have one? Sign in',
      hintIn: 'Use the email and password you signed up with.',
      hintUp: 'Email and the password twice — nothing is mailed to that address. To leave yourself a way back, add a question only you can answer below.',
      questionGroup: 'If you forget the password (optional)',
      hintClaim: 'This phone is still connected to {name}. Set an email and a password and it becomes your account — the TV needs no new code.',
      badEmail: 'That does not look like an email address.', shortPassword: 'At least eight characters.',
      commonPassword: 'That password is too common — pick another.',
      taken: 'That email already has an account. Sign in instead.', wrong: 'Wrong email or password.',
      rate: 'Too many tries. Wait a few minutes.',
      locked: 'Too many wrong passwords. Try again in fifteen minutes.',
      retry: 'That step did not go through — tap again.',
      noReset: 'No mail here: forgetting the password means answering your own question and setting a new one.',
      ruleLength: 'Eight characters or more', ruleDigits: 'Not digits only',
      ruleNotEmail: 'Not the first half of your email', ruleCommon: 'Not 12345678 and friends',
      ruleMatch: 'Both passwords match',
      forgot: 'Forgot the password?',
      recoverTitle: 'Get back in', hintRecover: 'Type the email you registered with and we will show your question.',
      hintRecover2: 'Answer it and you can set a new password right away.',
      askQuestion: 'Show my question', setPassword: 'Set new password',
      questionIs: 'Your question: {question}',
      changeTitle: 'Change password',
      hintChange: 'Type the current password, then the new one twice. This phone signs out too.',
      changeSubmit: 'Change password',
      mismatch: 'The two passwords are not the same.',
      needQuestion: 'Write a question only you can answer.', needAnswer: 'Write the answer down — it is the only way back in.',
      badAnswer: 'That is not the answer.', recoverLocked: 'Too many wrong answers. Try again in fifteen minutes.',
      noAccount: 'No account with that email.',
      noQuestion: 'This account has no question, so this route does not apply.',
      samePassword: 'That is the password you already have.', weakPassword: 'Not digits only.',
      changed: 'Password changed. Sign in with the new one.', resetDone: 'Password reset. Sign in with the new one.',
      setQuestionTitle: 'Recovery question', hintSetQuestion: 'This is how you get back in. Replacing it retires the old answer.',
      saveQuestion: 'Save', questionSaved: 'Saved.',
      signOut: 'Sign out', switch: 'Switch TV', addTv: 'Connect another TV',
      devices: 'Televisions on this account', current: 'Watching',
    },
    today: {
      left: 'Left today',
      unlimited: 'No limit today',
      used: 'Watched today', limit: 'Daily limit', earned: 'Earned by quizzes',
      passed: 'Passed', attempts: 'Tried today', times: '{n}×', canDo: 'Can still try',
      cooldown: 'Resting', cooldownNote: 'A little longer before the next group',
      noreport: 'The TV has not reported yet',
      noreportHint: 'It reports every half hour while it is on. This number arrives when it wakes.',
      hint: 'Numbers come from the TV, up to half an hour behind; time added with the password is not counted.',
      extra: 'plus any time added with the password',
    },
    settings: {
      daily: 'Daily watching time', session: 'One session', interval: 'Break',
      unlimited: 'No limit', minutes: '{n} min', hours: '{h} h', hoursMinutes: '{h} h {m} min',
      saved: 'Saved', delay: 'Applies within five minutes while the TV is on.',
      noteDaily: 'No limit means the day is not capped.',
      noteSession: 'With no session limit the break never triggers.',
      noteInterval: 'How long the break lasts after a session.',
      unpair: 'Unpair this phone',
      unpairConfirm: 'You will need a new code on the TV. Continue?',
      change: 'Change password', setQuestion: 'Recovery question',
      language: 'Language',
    },
    wrong: {
      empty: 'No mistakes recently', emptyHint: 'Wrong answers show up here with the right answer and the explanation.',
      yours: 'They said', right: 'Answer', days: 'Last {n} days',
      flagged: 'Marked as not learned',
    },
    week: {
      attempts: 'Tries', passed: 'Passed', rate: 'Pass rate', earned: 'Earned',
      worst: 'Weakest topics', none: 'No wrong answers yet',
      chart: 'How long, day by day',
      legendUsed: 'other watching', legendEarned: 'earned by quizzes',
      empty: 'Nothing recorded this week',
      times: '{n}×',
    },
    common: { retry: 'Retry', close: 'Close', loading: 'Loading…', error: 'Something went wrong. Try again.' },
  },

  ru: {
    app: { parent: 'Родитель' },
    tab: { today: 'Сегодня', week: 'Неделя', wrong: 'Ошибки', settings: 'Настройки' },
    unit: { h: 'ч', m: 'мин', sep: ' ' },
    pair: {
      title: 'Подключить телевизор',
      hint: 'На телевизоре откройте «Настройки → Родительский режим → Сопряжение с телефоном» и введите шесть символов.',
      code: 'Код сопряжения',
      case: 'Код чувствителен к регистру — введите его точно так, как на экране.',
      submit: 'Подключить',
      ttl: 'Код действует десять минут. Если истёк — создайте новый на телевизоре.',
      wrong: 'Код неверный. Посмотрите на телевизор ещё раз — регистр важен.',
      expired: 'Срок кода истёк. Создайте новый на телевизоре.',
      rate: 'Слишком много попыток. Подождите несколько минут.',
      revoked: 'Этот телевизор отвязан.',
      net: 'Нет связи с сервером. Телефон в домашней сети Wi-Fi?',
    },
    auth: {
      signInTitle: 'Вход', signUpTitle: 'Создать аккаунт', claimTitle: 'Сделать аккаунт',
      signIn: 'Войти', signUp: 'Создать', claim: 'Готово',
      email: 'Почта', password: 'Пароль', password2: 'Пароль ещё раз', newPassword: 'Новый пароль',
      questionLabel: 'Вопрос для восстановления', answerLabel: 'Ответ', oldPassword: 'Текущий пароль',
      toSignUp: 'Нет аккаунта? Создайте', toSignIn: 'Уже есть? Войдите',
      hintIn: 'Почта и пароль, с которыми вы регистрировались.',
      hintUp: 'Почта и пароль дважды — письма на этот адрес не отправляются. Чтобы оставить путь назад, добавьте ниже свой вопрос.',
      questionGroup: 'Если забудете пароль (необязательно)',
      hintClaim: 'Этот телефон всё ещё подключён к «{name}». Задайте почту и пароль — телевизор заново подключать не нужно.',
      badEmail: 'Это не похоже на адрес почты.', shortPassword: 'Не короче восьми символов.',
      commonPassword: 'Слишком простой пароль, выберите другой.',
      taken: 'Такая почта уже зарегистрирована. Войдите.', wrong: 'Неверная почта или пароль.',
      rate: 'Слишком много попыток. Подождите.',
      locked: 'Слишком много ошибок. Попробуйте через пятнадцать минут.',
      retry: 'Этот шаг не прошёл — нажмите ещё раз.',
      noReset: 'Писем нет: забыли пароль — ответьте на свой вопрос и задайте новый.',
      ruleLength: 'Не короче 8 символов', ruleDigits: 'Не только цифры',
      ruleNotEmail: 'Не начало вашей почты', ruleCommon: 'Не 12345678 и подобное',
      ruleMatch: 'Оба пароля совпадают',
      forgot: 'Забыли пароль?',
      recoverTitle: 'Вернуться в аккаунт', hintRecover: 'Введите почту, с которой регистрировались, — покажем ваш вопрос.',
      hintRecover2: 'Ответьте на вопрос и сразу задайте новый пароль.',
      askQuestion: 'Показать вопрос', setPassword: 'Задать новый пароль',
      questionIs: 'Ваш вопрос: {question}',
      changeTitle: 'Смена пароля',
      hintChange: 'Введите текущий пароль, затем дважды новый. Этот телефон тоже выйдет из аккаунта.',
      changeSubmit: 'Сменить пароль',
      mismatch: 'Пароли не совпадают.',
      needQuestion: 'Напишите вопрос, ответ на который знаете только вы.', needAnswer: 'Запишите ответ — на нём всё держится.',
      badAnswer: 'Ответ неверный.', recoverLocked: 'Слишком много неверных ответов. Через пятнадцать минут.',
      noAccount: 'Такая почта не зарегистрирована.',
      noQuestion: 'У этого аккаунта нет вопроса, этот способ не подойдёт.',
      samePassword: 'Это и есть текущий пароль.', weakPassword: 'Не только цифры.',
      changed: 'Пароль изменён. Войдите с новым.', resetDone: 'Пароль сброшен. Войдите с новым.',
      setQuestionTitle: 'Вопрос для восстановления', hintSetQuestion: 'По нему вы вернётесь в аккаунт. Замените — старый ответ перестанет работать.',
      saveQuestion: 'Сохранить', questionSaved: 'Сохранено.',
      signOut: 'Выйти', switch: 'Сменить телевизор', addTv: 'Подключить другой телевизор',
      devices: 'Телевизоры аккаунта', current: 'Сейчас',
    },
    today: {
      left: 'Осталось сегодня',
      unlimited: 'Сегодня без ограничений',
      used: 'Просмотрено сегодня', limit: 'Дневной лимит', earned: 'Заработано заданиями',
      passed: 'Сдано', attempts: 'Попыток сегодня', times: '{n} раз', canDo: 'Можно ещё',
      cooldown: 'Перерыв', cooldownNote: 'Подождите немного до следующего набора',
      noreport: 'Телевизор ещё не отчитался',
      noreportHint: 'Он сообщает каждые полчаса, пока включён. Цифра появится, когда он включится.',
      hint: 'Данные с телевизора, отстают до получаса; время, добавленное паролем, не учтено.',
      extra: 'не считая времени, добавленного паролем',
    },
    settings: {
      daily: 'Время просмотра в день', session: 'Один сеанс', interval: 'Перерыв',
      unlimited: 'Без ограничений', minutes: '{n} мин', hours: '{h} ч', hoursMinutes: '{h} ч {m} мин',
      saved: 'Сохранено', delay: 'Применится в течение пяти минут, если телевизор включён.',
      noteDaily: '«Без ограничений» — день не ограничен.',
      noteSession: 'Если сеанс без ограничения, перерыв не наступит.',
      noteInterval: 'Сколько длится перерыв после сеанса.',
      unpair: 'Отвязать этот телефон',
      unpairConfirm: 'Понадобится новый код на телевизоре. Продолжить?',
      change: 'Сменить пароль', setQuestion: 'Вопрос для восстановления',
      language: 'Язык',
    },
    wrong: {
      empty: 'Ошибок недавно нет', emptyHint: 'Здесь появятся неверные ответы с правильным ответом и разбором.',
      yours: 'Ответил', right: 'Ответ', days: 'Последние {n} дней',
      flagged: 'Отмечено как неизученное',
    },
    week: {
      attempts: 'Попытки', passed: 'Сдано', rate: 'Успех', earned: 'Заработано',
      worst: 'Слабые темы', none: 'Ошибок пока нет',
      chart: 'Сколько смотрели — по дням',
      legendUsed: 'прочий просмотр', legendEarned: 'заработано заданиями',
      empty: 'За эту неделю записей нет',
      times: '{n} раз',
    },
    common: { retry: 'Ещё раз', close: 'Закрыть', loading: 'Загрузка…', error: 'Что-то пошло не так. Попробуйте позже.' },
  },

  uk: {
    app: { parent: 'Батьки' },
    tab: { today: 'Сьогодні', week: 'Тиждень', wrong: 'Помилки', settings: 'Налаштування' },
    unit: { h: 'год', m: 'хв', sep: ' ' },
    pair: {
      title: 'Підключити телевізор',
      hint: 'На телевізорі відкрийте «Налаштування → Батьківський режим → Сполучення з телефоном» і введіть шість символів.',
      code: 'Код сполучення',
      case: 'Код чутливий до регістру — введіть його точно як на екрані.',
      submit: 'Підключити',
      ttl: 'Код діє десять хвилин. Якщо минув — створіть новий на телевізорі.',
      wrong: 'Код неправильний. Подивіться на телевізор ще раз — регістр важливий.',
      expired: 'Термін коду минув. Створіть новий на телевізорі.',
      rate: 'Забагато спроб. Зачекайте кілька хвилин.',
      revoked: 'Цей телевізор від’єднано.',
      net: 'Немає зв’язку з сервером. Телефон у домашній мережі Wi-Fi?',
    },
    auth: {
      signInTitle: 'Вхід', signUpTitle: 'Створити обліковий запис', claimTitle: 'Зробити обліковий запис',
      signIn: 'Увійти', signUp: 'Створити', claim: 'Готово',
      email: 'Пошта', password: 'Пароль', password2: 'Пароль ще раз', newPassword: 'Новий пароль',
      questionLabel: 'Питання для відновлення', answerLabel: 'Відповідь', oldPassword: 'Поточний пароль',
      toSignUp: 'Ще немає запису? Створіть', toSignIn: 'Уже є? Увійдіть',
      hintIn: 'Пошта й пароль, з якими ви реєструвалися.',
      hintUp: 'Пошта й пароль двічі — листи на цю адресу не надсилаються. Щоб залишити шлях назад, додайте нижче своє питання.',
      questionGroup: 'Якщо забудете пароль (необовʼязково)',
      hintClaim: 'Цей телефон ще підʼєднаний до «{name}». Задайте пошту й пароль — телевізор не треба підключати заново.',
      badEmail: 'Це не схоже на адресу пошти.', shortPassword: 'Не коротше восьми символів.',
      commonPassword: 'Занадто простий пароль, виберіть інший.',
      taken: 'Така пошта вже зареєстрована. Увійдіть.', wrong: 'Невірна пошта або пароль.',
      rate: 'Забагато спроб. Зачекайте.',
      locked: 'Забагато помилок. Спробуйте за пʼятнадцять хвилин.',
      retry: 'Цей крок не пройшов — натисніть ще раз.',
      noReset: 'Листів немає: забули пароль — дайте відповідь на своє питання й задайте новий.',
      ruleLength: 'Не коротше 8 символів', ruleDigits: 'Не лише цифри',
      ruleNotEmail: 'Не початок вашої пошти', ruleCommon: 'Не 12345678 і подібне',
      ruleMatch: 'Обидва паролі збігаються',
      forgot: 'Забули пароль?',
      recoverTitle: 'Повернутися до запису', hintRecover: 'Введіть пошту, з якою реєструвалися, — покажемо ваше питання.',
      hintRecover2: 'Дайте відповідь і одразу задайте новий пароль.',
      askQuestion: 'Показати питання', setPassword: 'Задати новий пароль',
      questionIs: 'Ваше питання: {question}',
      changeTitle: 'Зміна пароля',
      hintChange: 'Введіть поточний пароль, потім двічі новий. Цей телефон теж вийде з запису.',
      changeSubmit: 'Змінити пароль',
      mismatch: 'Паролі не збігаються.',
      needQuestion: 'Напишіть питання, відповідь на яке знаєте тільки ви.', needAnswer: 'Запишіть відповідь — на ній усе тримається.',
      badAnswer: 'Відповідь неправильна.', recoverLocked: 'Забагато неправильних відповідей. За пʼятнадцять хвилин.',
      noAccount: 'Така пошта не зареєстрована.',
      noQuestion: 'У цього запису немає питання, цей спосіб не підійде.',
      samePassword: 'Це і є поточний пароль.', weakPassword: 'Не лише цифри.',
      changed: 'Пароль змінено. Увійдіть із новим.', resetDone: 'Пароль скинуто. Увійдіть із новим.',
      setQuestionTitle: 'Питання для відновлення', hintSetQuestion: 'За ним ви повернетеся до запису. Заміните — стара відповідь перестане діяти.',
      saveQuestion: 'Зберегти', questionSaved: 'Збережено.',
      signOut: 'Вийти', switch: 'Змінити телевізор', addTv: 'Підключити інший телевізор',
      devices: 'Телевізори запису', current: 'Зараз',
    },
    today: {
      left: 'Залишилось сьогодні',
      unlimited: 'Сьогодні без обмежень',
      used: 'Переглянуто сьогодні', limit: 'Денний ліміт', earned: 'Зароблено завданнями',
      passed: 'Складено', attempts: 'Спроб сьогодні', times: '{n} разів', canDo: 'Можна ще',
      cooldown: 'Перерва', cooldownNote: 'Зачекайте трохи до наступного набору',
      noreport: 'Телевізор ще не звітував',
      noreportHint: 'Він повідомляє щопівгодини, поки ввімкнений. Цифра з’явиться, коли він увімкнеться.',
      hint: 'Дані з телевізора, відстають до півгодини; час, доданий паролем, не враховано.',
      extra: 'не рахуючи часу, доданого паролем',
    },
    settings: {
      daily: 'Час перегляду на день', session: 'Один сеанс', interval: 'Перерва',
      unlimited: 'Без обмежень', minutes: '{n} хв', hours: '{h} год', hoursMinutes: '{h} год {m} хв',
      saved: 'Збережено', delay: 'Застосується протягом п’яти хвилин, якщо телевізор увімкнений.',
      noteDaily: '«Без обмежень» — день не обмежений.',
      noteSession: 'Якщо сеанс без обмеження, перерва не настане.',
      noteInterval: 'Скільки триває перерва після сеансу.',
      unpair: 'Від’єднати цей телефон',
      unpairConfirm: 'Знадобиться новий код на телевізорі. Продовжити?',
      change: 'Змінити пароль', setQuestion: 'Питання для відновлення',
      language: 'Мова',
    },
    wrong: {
      empty: 'Помилок нещодавно немає', emptyHint: 'Тут з’являться неправильні відповіді з правильною відповіддю та розбором.',
      yours: 'Відповів', right: 'Відповідь', days: 'Останні {n} днів',
      flagged: 'Позначено як невивчене',
    },
    week: {
      attempts: 'Спроби', passed: 'Складено', rate: 'Успіх', earned: 'Зароблено',
      worst: 'Найслабші теми', none: 'Помилок поки немає',
      chart: 'Скільки дивилися — за днями',
      legendUsed: 'інший перегляд', legendEarned: 'зароблено завданнями',
      empty: 'За цей тиждень записів немає',
      times: '{n} разів',
    },
    common: { retry: 'Ще раз', close: 'Закрити', loading: 'Завантаження…', error: 'Щось пішло не так. Спробуйте пізніше.' },
  },

  kk: {
    app: { parent: 'Ата-ана' },
    tab: { today: 'Бүгін', week: 'Апта', wrong: 'Қателер', settings: 'Параметрлер' },
    unit: { h: 'сағ', m: 'мин', sep: ' ' },
    pair: {
      title: 'Теледидарды қосу',
      hint: 'Теледидарда «Параметрлер → Ата-ана режимі → Телефонмен жұптау» бөлімін ашып, алты таңбаны енгізіңіз.',
      code: 'Жұптау коды',
      case: 'Код регистрге сезімтал — экрандағыдай дәл енгізіңіз.',
      submit: 'Қосу',
      ttl: 'Код он минут жарамды. Мерзімі бітсе — теледидарда жаңасын жасаңыз.',
      wrong: 'Код дұрыс емес. Теледидарға тағы қараңыз — регистр маңызды.',
      expired: 'Кодтың мерзімі бітті. Теледидарда жаңасын жасаңыз.',
      rate: 'Тым көп әрекет. Бірнеше минут күтіңіз.',
      revoked: 'Бұл теледидар ажыратылған.',
      net: 'Серверге қосылу мүмкін емес. Телефон үйдегі Wi-Fi желісінде ме?',
    },
    auth: {
      signInTitle: 'Кіру', signUpTitle: 'Аккаунт құру', claimTitle: 'Аккаунт ету',
      signIn: 'Кіру', signUp: 'Құру', claim: 'Дайын',
      email: 'Пошта', password: 'Құпиясөз', password2: 'Құпиясөзді қайталаңыз', newPassword: 'Жаңа құпиясөз',
      questionLabel: 'Қалпына келтіру сұрағы', answerLabel: 'Жауап', oldPassword: 'Қазіргі құпиясөз',
      toSignUp: 'Аккаунтыңыз жоқ па? Құрыңыз', toSignIn: 'Бар ма? Кіріңіз',
      hintIn: 'Тіркелген поштаңыз бен құпиясөзіңіз.',
      hintUp: 'Пошта және құпиясөз екі рет — бұл мекенжайға хат жіберілмейді. Қайту жолын қалдыру үшін төменде өз сұрағыңызды қосыңыз.',
      questionGroup: 'Құпиясөзді ұмытсаңыз (міндетті емес)',
      hintClaim: 'Бұл телефон «{name}» құрылғысына әлі қосылған. Пошта мен құпиясөз қойыңыз — теледидарды қайта қосу қажет емес.',
      badEmail: 'Бұл пошта мекенжайына ұқсамайды.', shortPassword: 'Кемінде сегіз таңба.',
      commonPassword: 'Бұл құпиясөз тым қарапайым, басқасын таңдаңыз.',
      taken: 'Бұл пошта тіркелген. Кіріңіз.', wrong: 'Пошта немесе құпиясөз қате.',
      rate: 'Әрекет тым көп. Сәл күте тұрыңыз.',
      locked: 'Қате тым көп. Он бес минуттан кейін көріңіз.',
      retry: 'Бұл қадам өтпеді — қайта басыңыз.',
      noReset: 'Хат жоқ: құпиясөзді ұмытсаңыз, өз сұрағыңызға жауап беріп, жаңасын қоясыз.',
      ruleLength: 'Кемінде 8 таңба', ruleDigits: 'Тек цифр емес',
      ruleNotEmail: 'Поштаңыздың басы емес', ruleCommon: '12345678 сияқтылар емес',
      ruleMatch: 'Екі құпиясөз бірдей',
      forgot: 'Құпиясөзді ұмыттыңыз ба?',
      recoverTitle: 'Аккаунтқа қайту', hintRecover: 'Тіркелген поштаны енгізіңіз — сұрағыңызды көрсетеміз.',
      hintRecover2: 'Жауап берсеңіз, бірден жаңа құпиясөз қоя аласыз.',
      askQuestion: 'Сұрағымды көрсет', setPassword: 'Жаңа құпиясөз қою',
      questionIs: 'Сіздің сұрағыңыз: {question}',
      changeTitle: 'Құпиясөзді ауыстыру',
      hintChange: 'Қазіргі құпиясөзді, содан кейін жаңасын екі рет енгізіңіз. Бұл телефон да шығады.',
      changeSubmit: 'Құпиясөзді ауыстыру',
      mismatch: 'Құпиясөздер сәйкес емес.',
      needQuestion: 'Жауабын тек сіз білетін сұрақ жазыңыз.', needAnswer: 'Жауапты жазып алыңыз — бәрі соған тіреледі.',
      badAnswer: 'Жауап қате.', recoverLocked: 'Қате жауап тым көп. Он бес минуттан кейін.',
      noAccount: 'Бұл пошта тіркелмеген.',
      noQuestion: 'Бұл аккаунтта сұрақ жоқ, бұл жол келмейді.',
      samePassword: 'Бұл қазіргі құпиясөз.', weakPassword: 'Тек цифр емес.',
      changed: 'Құпиясөз ауыстырылды. Жаңасымен кіріңіз.', resetDone: 'Құпиясөз қалпына келтірілді. Жаңасымен кіріңіз.',
      setQuestionTitle: 'Қалпына келтіру сұрағы', hintSetQuestion: 'Осы арқылы қайтасыз. Ауыстырсаңыз, ескі жауап күшін жояды.',
      saveQuestion: 'Сақтау', questionSaved: 'Сақталды.',
      signOut: 'Шығу', switch: 'Теледидар ауыстыру', addTv: 'Басқа теледидар қосу',
      devices: 'Аккаунттағы теледидарлар', current: 'Қазір',
    },
    today: {
      left: 'Бүгін қалды',
      unlimited: 'Бүгін шектеусіз',
      used: 'Бүгін көрілді', limit: 'Күндік шек', earned: 'Тапсырмадан жиналды',
      passed: 'Тапсырылды', attempts: 'Бүгінгі әрекет', times: '{n} рет', canDo: 'Тағы болады',
      cooldown: 'Демалыс', cooldownNote: 'Келесі топқа дейін сәл күтіңіз',
      noreport: 'Теледидар әлі хабарлаған жоқ',
      noreportHint: 'Ол қосулы тұрғанда жарты сағат сайын хабарлайды. Сан ол қосылғанда келеді.',
      hint: 'Деректер теледидардан, жарты сағатқа дейін кешігуі мүмкін; құпиясөзбен қосылған уақыт есептелмейді.',
      extra: 'құпиясөзбен қосылған уақытты есептемегенде',
    },
    settings: {
      daily: 'Күндік көру уақыты', session: 'Бір сеанс', interval: 'Үзіліс',
      unlimited: 'Шектеусіз', minutes: '{n} мин', hours: '{h} сағ', hoursMinutes: '{h} сағ {m} мин',
      saved: 'Сақталды', delay: 'Теледидар қосулы болса, бес минут ішінде қолданылады.',
      noteDaily: '«Шектеусіз» — күн шектелмейді.',
      noteSession: 'Сеанс шектеусіз болса, үзіліс басталмайды.',
      noteInterval: 'Сеанстан кейінгі үзіліс қаншаға созылады.',
      unpair: 'Бұл телефонды ажырату',
      unpairConfirm: 'Теледидарда жаңа код қажет болады. Жалғастыру керек пе?',
      change: 'Құпиясөзді ауыстыру', setQuestion: 'Қалпына келтіру сұрағы',
      language: 'Тіл',
    },
    wrong: {
      empty: 'Жақында қате жоқ', emptyHint: 'Қате жауаптар дұрыс жауап пен түсіндірмемен осында шығады.',
      yours: 'Жауабы', right: 'Дұрыс жауап', days: 'Соңғы {n} күн',
      flagged: 'Оқылмаған деп белгіленді',
    },
    week: {
      attempts: 'Әрекет', passed: 'Тапсырылды', rate: 'Табыс', earned: 'Жиналды',
      worst: 'Ең әлсіз тақырыптар', none: 'Әзірге қате жоқ',
      chart: 'Күн сайын қанша көрілді',
      legendUsed: 'қалған көру', legendEarned: 'тапсырмадан жиналғаны',
      empty: 'Бұл аптада жазба жоқ',
      times: '{n} рет',
    },
    common: { retry: 'Қайталау', close: 'Жабу', loading: 'Жүктелуде…', error: 'Бірдеңе дұрыс болмады. Кейінірек көріңіз.' },
  },
};

// ---------------------------------------------------------------- state

let lang = 'zh';
let view = 'today';
/** One slot per view, deliberately keyed the same as the routes: `go()` fills `cache[name]`, and
 *  nothing else writes it except a logout, which clears the lot. */
const cache = {};

/** The signed-in account: `{ account, devices }`. Null until `/v1/auth/me` answers. */
let me = null;
/** Which television is on screen. One account can hold several (docs/ACCOUNT.md, A2), and every
 *  per-device route carries this id — the server no longer infers it from the cookie. */
let deviceId = localStorage.getItem('aq.device') || null;
/**
 * `in` (sign in), `up` (register), `claim` (a phone that paired before accounts existed),
 * `recover` (type the email), `recover2` (answer the question, set a new password) or `change`
 * (old password, new password, while signed in).
 */
let authMode = 'in';
/** The registration nonce; see `startAuth`. Also the reason a registration cannot be posted twice. */
let challenge = null;
/** The pending `startAuth()`, so a submit that beats the network can wait for the nonce instead of
 *  posting `challenge: null` and getting a 400 that says nothing. */
let challengePending = null;
/** The device a claim is about, so the copy can name it. */
let claimDevice = null;
/** Recover, second half: which account was looked up and the question it answered. The email field is
 *  read-only there, so the answer cannot be redirected to another account by editing it. */
let recoverEmail = null;
let recoverQuestion = null;

const signedOut = err => err.code === 'not-signed-in' || err.code === 'not-paired';

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

function T() { return TEXTS[lang]; }

function fill(template, values) {
  return template.replace(/\{(\w+)\}/g, (_, key) => (key in values ? String(values[key]) : `{${key}}`));
}

function esc(value) {
  return String(value == null ? '' : value)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

/** The local accounting day, turning over at 04:00 — same rule as the TV. Built from calendar
 *  fields rather than by subtracting four hours from a timestamp: the instant version moves the
 *  roll-over to 05:00 while daylight saving is in effect. */
function dayKey(date = new Date()) {
  const d = new Date(date.getFullYear(), date.getMonth(), date.getDate());
  if (date.getHours() < 4) d.setDate(d.getDate() - 1);
  const pad = n => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

function fmtDuration(ms) {
  if (ms == null) return '—';
  const unit = T().unit;
  const total = Math.round(ms / 60000);
  const h = Math.floor(total / 60);
  const m = total % 60;
  if (h && m) return `${h}${unit.sep}${unit.h}${unit.sep}${m}${unit.sep}${unit.m}`;
  if (h) return `${h}${unit.sep}${unit.h}`;
  return `${m}${unit.sep}${unit.m}`;
}

function fmtMinutes(minutes) {
  const s = T().settings;
  if (minutes === 0) return s.unlimited;
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  if (h && m) return fill(s.hoursMinutes, { h, m });
  if (h) return fill(s.hours, { h });
  return fill(s.minutes, { n: m });
}

// ---------------------------------------------------------------- network

async function api(path, options = {}) {
  const init = { credentials: 'same-origin', headers: {}, ...options };
  if (init.body !== undefined && typeof init.body !== 'string') {
    init.headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(init.body);
  }
  let response;
  try {
    response = await fetch(path, init);
  } catch (err) {
    throw Object.assign(new Error('offline'), { code: 'offline' });
  }
  let data = {};
  try { data = await response.json(); } catch (err) { /* empty body is fine */ }
  if (response.status === 401) {
    // A stale or missing session is a state, not a failure: the answer is the sign-in screen.
    // Everything but the credential endpoints themselves lands on the sign-in screen; asking
    // `/v1/auth/me` with no session is how the page finds out it has to.
    if (!path.startsWith('/v1/auth/') || path === '/v1/auth/me') showAuth('in');
    throw Object.assign(new Error(data.error || 'not-paired'),
      { code: data.error || 'not-paired', status: 401 });
  }
  if (!response.ok) {
    throw Object.assign(new Error(data.error || String(response.status)),
      { code: data.error || 'error', status: response.status, detail: data.detail });
  }
  return data;
}

// ---------------------------------------------------------------- chrome

let toastTimer = null;
function toast(message) {
  const el = $('#toast');
  el.textContent = message;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, 2200);
}

function applyLanguage() {
  document.documentElement.lang = LOCALE[lang];
  $('#lang').textContent = CHIP[lang];
  $$('[data-i18n]').forEach(node => {
    const key = node.dataset.i18n;
    const [group, name] = key.split('.');
    const value = T()[group] && T()[group][name];
    if (value) node.textContent = value;
  });
  $('#pair-code').setAttribute('aria-label', T().pair.code);
  if (!$('#screen-auth').hidden) {
    showAuth(authMode, claimDevice);
    return;
  }
  refresh();
}

function showPair() {
  $('#screen-auth').hidden = true;
  $('#screen-main').hidden = true;
  $('#screen-pair').hidden = false;
  $('#pair-code').focus({ preventScroll: true });
}

function showMain() {
  $('#screen-auth').hidden = true;
  $('#screen-pair').hidden = true;
  $('#screen-main').hidden = false;
}

/**
 * The sign-in / sign-up / claim / recover / change screen. One form for six jobs because they ask
 * for overlapping fields; what changes is which fields, the sentence above them, and the button.
 * `data-modes` on each field lists the modes it belongs to, so a new mode is one attribute.
 */
function showAuth(mode, context) {
  authMode = mode || 'in';
  claimDevice = context || null;
  $('#screen-main').hidden = true;
  $('#screen-pair').hidden = true;
  $('#screen-auth').hidden = false;
  const a = T().auth;
  const titles = {
    in: a.signInTitle, up: a.signUpTitle, claim: a.claimTitle,
    recover: a.recoverTitle, recover2: a.recoverTitle, change: a.changeTitle,
    question: a.setQuestionTitle,
  };
  const hints = {
    in: a.hintIn, up: a.hintUp,
    claim: fill(a.hintClaim, { name: (claimDevice && claimDevice.name) || 'Aerial' }),
    recover: a.hintRecover, recover2: a.hintRecover2, change: a.hintChange,
    question: a.hintSetQuestion,
  };
  const buttons = {
    in: a.signIn, up: a.signUp, claim: a.claim,
    recover: a.askQuestion, recover2: a.setPassword, change: a.changeSubmit,
    question: a.saveQuestion,
  };
  $('#auth-title').textContent = titles[authMode] || a.signInTitle;
  $('#auth-hint').textContent = hints[authMode] || a.hintIn;
  $('#auth-submit').textContent = buttons[authMode] || a.signIn;

  // Offered on the recovery screen too: "no account with that email" is exactly the moment the
  // parent needs the sign-up link, and leaving them on a dead screen is how a form loses people.
  $('#auth-switch-line').hidden =
    !(authMode === 'in' || authMode === 'up' || authMode === 'recover');
  $('#auth-switch').textContent = authMode === 'up' ? a.toSignIn : a.toSignUp;
  $('#auth-forgot-line').hidden = authMode !== 'in';
  // Only under the sign-in form: the sign-up hint already says nothing is mailed to the
  // address, and repeating it there adds a paragraph the screen does not have room for.
  $('#auth-noreset').hidden = authMode !== 'in';
  if (authMode === 'recover2') {
    $('#auth-question').textContent = fill(a.questionIs, { question: recoverQuestion || '' });
  }
  $('#auth-email').readOnly = authMode === 'recover2' || authMode === 'change';
  // On the last two screens the field is a new password, and saying so is the whole point: typing
  // the old one there is the mistake this label prevents.
  $('#auth-password-label').textContent =
    authMode === 'recover2' || authMode === 'change' ? a.newPassword : a.password;
  $('#auth-password').setAttribute('autocomplete',
    authMode === 'in' || authMode === 'change' ? 'current-password' : 'new-password');

  ['#auth-password', '#auth-password2', '#auth-answer', '#auth-answer2', '#auth-question-input',
    '#auth-old'].forEach(id => { $(id).value = ''; });
  $('#auth-error').hidden = true;
  applyAuthMode();
  if (authMode === 'in' || authMode === 'change' || authMode === 'recover2'
      || authMode === 'question') {
    challenge = null;                      // nothing is being registered, so no nonce to fetch
  } else {
    startAuth();
  }
  paintRules();
  const first = authMode === 'change' ? '#auth-old'
    : authMode === 'question' ? '#auth-question-input'
      : authMode === 'recover2' ? '#auth-answer2' : '#auth-email';
  $(first).focus({ preventScroll: true });
}

/** Shows the fields that belong to this mode. Everything else keeps its value but is not seen. */
function applyAuthMode() {
  $$('#auth-form [data-modes]').forEach(node => {
    node.hidden = !node.dataset.modes.split(/\s+/).includes(authMode);
  });
}

//: The obvious half of `password_problem()` in server/app/auth.py. The server is still the judge;
//: this list exists so the rules can light up while typing instead of after being told no.
const WEAK_LOCAL = ['password', 'password1', 'password12', '12345678', '123456789', '1234567890',
  '11111111', '88888888', '66666666', 'qwertyui', 'qwerty123', 'abc12345', 'iloveyou',
  'aaaaaaaa', 'woaini1314', '5201314520'];

/** Which rules the form currently satisfies. Empty fields count as unmet, not as errors. */
function ruleState() {
  const password = $('#auth-password').value;
  const again = $('#auth-password2').value;
  const local = ($('#auth-email').value.split('@')[0] || '').trim().toLowerCase();
  const flat = password.trim().toLowerCase();
  return {
    length: password.length >= 8,
    digits: password.length > 0 && !/^\d+$/.test(password),
    notemail: password.length > 0 && !(local && flat === local),
    common: password.length > 0 && !WEAK_LOCAL.includes(flat),
    match: again.length > 0 && password === again,
  };
}

function paintRules() {
  const box = $('#auth-rules');
  if (!box || box.hidden) return;
  const state = ruleState();
  $$('#auth-rules li').forEach(item => {
    item.classList.toggle('is-ok', !!state[item.dataset.rule]);
  });
}

/** Fetches a fresh challenge. Called on every entry to the form, so a page left open for an hour is
 *  still a page that can register — the nonce expires in ten minutes. */
function startAuth() {
  challenge = null;
  const button = $('#auth-submit');
  button.disabled = true;
  setTimeout(() => { button.disabled = false; }, 1300);
  challengePending = api('/v1/auth/challenge')
    .then(data => { challenge = data.challenge; })
    .catch(() => { /* offline: the submit reports it, and the button still looks normal */ });
  return challengePending;
}

// ---------------------------------------------------------------- pairing

function pairError(err) {
  const p = T().pair;
  switch (err.code) {
    case 'wrong-code': return p.wrong;
    case 'code-expired': return p.expired;
    case 'too-many-tries': return p.rate;
    case 'device-revoked': return p.revoked;
    case 'offline': return p.net;
    default: return T().common.error;
  }
}

async function submitPair(event) {
  event.preventDefault();
  const input = $('#pair-code');
  const button = $('#pair-submit');
  const error = $('#pair-error');
  const code = input.value.trim();
  error.hidden = true;

  if (code.length !== 6) {
    error.textContent = T().pair.case;
    error.hidden = false;
    return;
  }

  button.disabled = true;
  try {
    const result = await api('/v1/h5/pair', { method: 'POST', body: { pair_code: code } });
    me = { account: me && me.account, devices: result.devices || [] };
    deviceId = (result.device && result.device.device_id) || deviceId;
    if (deviceId) localStorage.setItem('aq.device', deviceId);
    pickDevice();
    input.value = '';
    Object.keys(cache).forEach(key => delete cache[key]);
    showMain();
    await go(view || 'today');
  } catch (err) {
    if (signedOut(err)) return;
    error.textContent = pairError(err);
    error.hidden = false;
    input.select();
  } finally {
    button.disabled = false;
  }
}

// ---------------------------------------------------------------- accounts

function authError(err) {
  const a = T().auth;
  switch (err.code) {
    case 'email-bad': return a.badEmail;
    case 'email-taken': return a.taken;
    case 'password-short':
    case 'password-long': return a.shortPassword;
    case 'password-common':
    case 'password-is-email': return a.commonPassword;
    case 'password-weak': return a.weakPassword;
    case 'password-same': return a.samePassword;
    case 'bad-credentials': return a.wrong;
    case 'login-rate':
    case 'signup-rate':
    case 'recover-rate':
    case 'too-many-tries': return a.rate;
    case 'account-locked': return a.locked;
    case 'challenge-bad': return a.retry;
    case 'question-short':
    case 'question-long': return a.needQuestion;
    case 'answer-short':
    case 'answer-long': return a.needAnswer;
    case 'bad-answer': return a.badAnswer;
    case 'recover-locked': return a.recoverLocked;
    case 'no-account': return a.noAccount;
    case 'no-question': return a.noQuestion;
    case 'offline': return T().pair.net;
    default: return T().common.error;
  }
}

async function submitAuth(event) {
  event.preventDefault();
  // Asking for the question is its own round trip: the answer field is not even on screen yet.
  if (authMode === 'recover') return askQuestion();

  const button = $('#auth-submit');
  const error = $('#auth-error');
  const email = $('#auth-email').value.trim();
  const password = $('#auth-password').value;
  error.hidden = true;

  const bad = checkLocally(email, password);
  if (bad) {
    error.textContent = bad;
    error.hidden = false;
    return;
  }

  const body = { email, password };
  if (authMode === 'question') {
    body.question = $('#auth-question-input').value.trim();
    body.answer = $('#auth-answer').value;
  } else if (authMode === 'change') {
    body.old_password = $('#auth-old').value;
  } else if (authMode === 'recover2') {
    body.answer = $('#auth-answer2').value;
    body.email = recoverEmail || email;
  } else if (authMode === 'up' || authMode === 'claim') {
    // Optional: only sent when the parent filled the group in. An empty pair is a valid account —
    // it can have a question added later from the settings page.
    const question = $('#auth-question-input').value.trim();
    if (question) {
      body.question = question;
      body.answer = $('#auth-answer').value;
    }
  }

  const path = authMode === 'in' ? '/v1/auth/login'
    : authMode === 'question' ? '/v1/auth/question/set'
      : authMode === 'change' ? '/v1/auth/password'
      : authMode === 'recover2' ? '/v1/auth/recover'
        : authMode === 'up' ? '/v1/auth/register' : '/v1/auth/claim';
  if (authMode === 'recover2' && !$('#auth-answer2').value.trim()) return a.needAnswer;
  if (authMode === 'up' || authMode === 'claim') {
    if (!challenge && challengePending) await challengePending;
    body.challenge = challenge;
    // The honeypot travels with the form because that is where a bot fills it in.
    body.website = $('#auth-website').value;
  }

  button.disabled = true;
  try {
    const result = await api(path, { method: 'POST', body });

    if (authMode === 'question') {
      toast(T().auth.questionSaved);
      showMain();
      await go('settings');
      return;
    }
    if (authMode === 'change') {
      // Every session died server-side, this one included. Signing in again is the proof that the
      // new password is one the parent can actually reproduce.
      me = null;
      deviceId = null;
      localStorage.removeItem('aq.device');
      Object.keys(cache).forEach(key => delete cache[key]);
      toast(T().auth.changed);
      showAuth('in');
      $('#auth-email').value = email;
      return;
    }
    if (authMode === 'recover2') {
      toast(fill(T().auth.resetDone, { email: result.email || body.email }));
      recoverEmail = null;
      recoverQuestion = null;
      showAuth('in');
      $('#auth-email').value = result.email || body.email;
      return;
    }

    me = { account: result.account, devices: result.devices || [] };
    if (result.device) me.devices = result.devices || [];
    $('#auth-password').value = '';
    $('#auth-password2').value = '';
    $('#auth-website').value = '';
    await enter();
  } catch (err) {
    if (signedOut(err)) return;
    // A spent or stale nonce is the one failure worth retrying by itself.
    if (authMode === 'up' || authMode === 'claim') await startAuth();
    error.textContent = authError(err);
    error.hidden = false;
    paintRules();
  } finally {
    button.disabled = false;
  }
}

/** The checks the phone can make without a server, said in the same order the server would. */
function checkLocally(email, password) {
  const a = T().auth;
  if (authMode === 'up' || authMode === 'claim') {
    // The question is optional; an answer without one is not. Checked in pairs so the form can
    // never post half a credential — the server refuses that too, but the parent should be told
    // before the round trip and before the button spends a registration challenge.
    const question = $('#auth-question-input').value.trim();
    const answer = $('#auth-answer').value.trim();
    if (question && !answer) return a.needAnswer;
    if (!question && answer) return a.needQuestion;
  }
  if (authMode === 'question') {
    if (!$('#auth-question-input').value.trim()) return a.needQuestion;
    if (!$('#auth-answer').value.trim()) return a.needAnswer;
  }
  if (authMode === 'up' || authMode === 'claim' || authMode === 'recover2' || authMode === 'change') {
    if (password !== $('#auth-password2').value) return a.mismatch;
    const state = ruleState();
    if (!state.length) return a.shortPassword;
    if (!state.digits) return a.weakPassword;
    if (!state.common) return a.commonPassword;
    if (!state.notemail) return a.commonPassword;
  }
  if (authMode === 'change' && !$('#auth-old').value) return a.wrong;
  // Neither of these screens has an email field: one is signed in already, the other is not about
  // an address at all.
  if (!email && authMode !== 'change' && authMode !== 'question') return a.badEmail;
  return null;
}

/** Recover, first half: look the question up so the parent can see what they are answering. */
async function askQuestion() {
  const button = $('#auth-submit');
  const error = $('#auth-error');
  const email = $('#auth-email').value.trim();
  error.hidden = true;
  if (!email) {
    error.textContent = T().auth.badEmail;
    error.hidden = false;
    return;
  }
  button.disabled = true;
  try {
    const data = await api('/v1/auth/question', { method: 'POST', body: { email } });
    recoverEmail = email;
    recoverQuestion = data.question;
    showAuth('recover2');
  } catch (err) {
    error.textContent = authError(err);
    error.hidden = false;
  } finally {
    button.disabled = false;
  }
}

/** Refreshes `me` from the server. Answers false when there is nobody signed in. */
async function refreshMe() {
  try {
    const data = await api('/v1/auth/me');
    if (data.legacy) {
      // A phone paired before accounts existed: one form away from being an account, and its
      // television comes along (docs/ACCOUNT.md, A5).
      me = null;
      showAuth('claim', data.device);
      return false;
    }
    me = { account: data.account, devices: data.devices || [] };
    return true;
  } catch (err) {
    if (!signedOut(err)) toast(authError(err));
    me = null;
    return false;
  }
}

/** Picks the television to look at: the one last used if it still exists, else the first. */
function pickDevice() {
  const devices = (me && me.devices) || [];
  const found = devices.find(d => d.device_id === deviceId) || devices[0] || null;
  deviceId = found ? found.device_id : null;
  if (deviceId) localStorage.setItem('aq.device', deviceId);
  else localStorage.removeItem('aq.device');
  if (found) $('#device-name').textContent = found.name || 'Aerial';
  return found;
}

/** Everything that happens once there is an account: the pair screen, or the four tabs. */
async function enter() {
  Object.keys(cache).forEach(key => delete cache[key]);
  if (!me || !me.devices.length) {
    showPair();
    return;
  }
  pickDevice();
  showMain();
  view = 'today';
  $$('.tab').forEach(tab => tab.classList.toggle('is-on', tab.dataset.tab === 'today'));
  try {
    const data = await load('today');
    cache.today = data;
    setDeviceName(data);
    $('#view').innerHTML = renderToday(data);
  } catch (err) {
    if (signedOut(err)) return;
    await go('today');
  }
}

// ---------------------------------------------------------------- device sheet

function openSheet() {
  const a = T().auth;
  const devices = (me && me.devices) || [];
  const rows = devices.map(d => `
    <button class="sheet-row${d.device_id === deviceId ? ' is-on' : ''}" data-device="${esc(d.device_id)}">
      <strong>${esc(d.name || 'Aerial')}</strong>
      <span>${d.device_id === deviceId ? esc(a.current) : ''}</span>
    </button>`).join('');
  $('#sheet-body').innerHTML = `<h2>${esc(a.devices)}</h2>${rows}
    <button class="sheet-row plain" id="sheet-add">${esc(a.addTv)}</button>
    <button class="sheet-row plain" id="sheet-signout">${esc(a.signOut)}</button>`;
  $('#sheet-backdrop').hidden = false;

  $$('[data-device]').forEach(row => row.addEventListener('click', async () => {
    closeSheet();
    if (row.dataset.device === deviceId) return;
    deviceId = row.dataset.device;
    localStorage.setItem('aq.device', deviceId);
    const found = pickDevice();
    if (found) $('#device-name').textContent = found.name || 'Aerial';
    Object.keys(cache).forEach(key => delete cache[key]);
    await go(view || 'today');
  }));
  $('#sheet-add').addEventListener('click', () => { closeSheet(); showPair(); });
  $('#sheet-signout').addEventListener('click', async () => {
    closeSheet();
    try { await api('/v1/auth/logout', { method: 'POST', body: {} }); } catch (err) { /* either way, out */ }
    me = null;
    deviceId = null;
    localStorage.removeItem('aq.device');
    Object.keys(cache).forEach(key => delete cache[key]);
    showAuth('in');
  });
}

function closeSheet() {
  $('#sheet-backdrop').hidden = true;
}

// ---------------------------------------------------------------- views

function skeleton() {
  return `<div class="card"><div class="skeleton" style="width:42%;height:12px"></div>
    <div class="skeleton" style="width:60%;height:34px;margin-top:12px"></div>
    <div class="skeleton" style="width:100%;height:9px;margin-top:16px"></div>
    <div class="skeleton" style="width:76%;height:12px;margin-top:14px"></div></div>`;
}

function emptyState(title, hint) {
  return `<div class="card"><div class="empty"><strong>${esc(title)}</strong>${esc(hint)}</div></div>`;
}

function setDeviceName(data) {
  const name = data && data.device && data.device.name;
  if (name) $('#device-name').textContent = name;
}

function renderToday(data) {
  const t = T().today;
  const today = data.today;
  const cap = today.unlimited ? null : today.budget_ms + today.awarded_ms;
  const used = today.used_ms;
  const left = cap == null ? null : Math.max(0, cap - (used || 0));

  let head;
  if (today.unlimited) {
    head = `<div class="headline"><span class="big small">${esc(t.unlimited)}</span></div>`;
  } else if (used == null) {
    // A 44px em dash reads as a black bar, not as "no number yet". Say it in words instead.
    head = `<div class="headline"><span class="big small muted">${esc(t.noreport)}</span></div>
      <p class="sub">${esc(t.noreportHint)}</p>`;
  } else {
    head = `<div class="headline"><span class="big">${Math.round(left / 60000)}</span>
      <span class="unit">${esc(T().unit.m)}</span></div>`;
  }

  const pct = cap && used != null ? Math.min(100, Math.round((used / cap) * 100)) : 0;
  const bar = today.unlimited ? '' : `<div class="bar"><i style="width:${pct}%"></i></div>`;

  const rows = [];
  if (!today.unlimited) {
    rows.push([t.used, used == null ? '—' : fmtDuration(used)]);
    rows.push([t.limit, fmtDuration(today.budget_ms)]);
  }
  if (today.awarded_ms > 0) rows.push([t.earned, fmtDuration(today.awarded_ms)]);
  // `passed` reuses `times` rather than a "N groups" string: a counter is the same shape in every
  // language, and "1 groups" is the kind of thing a hand-written plural always gets wrong somewhere.
  rows.push([t.passed, fill(t.times, { n: today.passed })]);
  rows.push([t.attempts, fill(t.times, { n: today.attempts })]);
  rows.push([t.canDo, fill(t.times, { n: today.attempts_left })]);
  if (today.cooldown_seconds > 0) rows.push([t.cooldown, `${today.cooldown_seconds}s`]);

  const hint = used == null ? '' : `<p class="footer-note">${esc(t.hint)}</p>`;

  return `<div class="card"><h2>${esc(t.left)}</h2>${head}${bar}${hint}</div>
    <div class="card"><div class="kv">${rows.map(([k, v]) =>
      `<div class="row"><span class="k">${esc(k)}</span><span class="v">${esc(v)}</span></div>`).join('')}
    </div></div>`;
}

// The three rows step in the same units the TV's settings page does (15 / 15 / 10), so a parent who
// sets 45 minutes here and then walks to the TV sees a number that is one of its stops.
const STEP = { daily_minutes: 15, session_minutes: 15, interval_minutes: 10 };
const MIN = { daily_minutes: 0, session_minutes: 0, interval_minutes: 10 };
const MAX = { daily_minutes: 720, session_minutes: 60, interval_minutes: 60 };

// The TV's daily row keeps one stop below the quarter-hours: 1 minute, for reaching the lock screen
// in a minute (docs/PARENTAL.md, 设置项). Step onto the same numbers, so 1 ∓ 15 is never 16 and
// never below zero — and the buttons offer 1 → 0 rather than greying "−" out at 1.
function nextSetting(key, value, delta) {
  if (key === 'daily_minutes') {
    if (delta > 0 && value === 1) return 15;
    if (delta < 0 && value === 15) return 1;
    if (delta < 0 && value === 1) return 0;
  }
  return value + delta * STEP[key];
}

function renderSettings(config) {
  const s = T().settings;
  const row = (key, label, note) => {
    const value = config[key];
    const down = nextSetting(key, value, -1) >= MIN[key];
    const up = nextSetting(key, value, 1) <= MAX[key];
    return `<div class="row">
      <span class="k">${esc(label)}</span>
      <span class="stepper">
        <button type="button" data-step="${key}" data-delta="-1" ${down ? '' : 'disabled'} aria-label="-">−</button>
        <span class="val">${esc(fmtMinutes(value))}</span>
        <button type="button" data-step="${key}" data-delta="1" ${up ? '' : 'disabled'} aria-label="+">+</button>
      </span></div>
      <p class="footer-note" style="margin:-4px 0 10px">${esc(note)}</p>`;
  };

  return `<div class="card"><div class="kv">
      ${row('daily_minutes', s.daily, s.noteDaily)}
      ${row('session_minutes', s.session, s.noteSession)}
      ${row('interval_minutes', s.interval, s.noteInterval)}
    </div></div>
    <p class="footer-note">${esc(s.delay)}</p>
    <div class="card"><div class="kv">
      <div class="row tap" id="setq"><span class="k">${esc(s.setQuestion)}</span>
        <span class="v dim">›</span></div>
      <div class="row tap" id="change"><span class="k">${esc(s.change)}</span>
        <span class="v dim">›</span></div>
      <div class="row tap" id="unpair"><span class="k" style="color:var(--bad)">${esc(s.unpair)}</span>
        <span class="v dim">›</span></div>
    </div></div>`;
}

function renderWrong(data) {
  const w = T().wrong;
  if (!data.items.length) return emptyState(w.empty, w.emptyHint);

  const cards = data.items.map(item => {
    const tags = [
      item.subject ? `<span class="pill">${esc(item.subject)}</span>` : '',
      item.topic ? `<span class="pill">${esc(item.topic)}</span>` : '',
      item.flagged_unknown ? `<span class="pill">${esc(w.flagged)}</span>` : '',
    ].join('');

    const yours = item.chosen_text || item.chosen || '—';
    const right = item.answer_text || '—';
    const why = item.explanation
      ? `<p class="why">${esc(item.explanation)}</p>` : '';

    return `<div class="q">
      <div class="head">${tags}</div>
      <p class="stem">${esc(item.stem)}</p>
      <div class="answer">
        <div class="line yours"><b>${esc(w.yours)}</b><span>${esc(yours)}</span></div>
        <div class="line right"><b>${esc(w.right)}</b><span>${esc(right)}</span></div>
      </div>
      ${why}
      <p class="meta">${esc(item.day_key)}${item.unit ? ' · ' + esc(item.unit) : ''}</p>
    </div>`;
  }).join('');

  return `<p class="footer-note" style="margin:2px 4px 0">${esc(fill(w.days, { n: data.days }))}</p>${cards}`;
}

function renderWeek(data) {
  const wk = T().week;
  const days = data.days;
  const anyUsed = days.some(d => d.used_ms != null);
  // The peak has to be the peak of the series actually drawn. It used to take the larger of the two
  // every day, so a day with a video and no report (used_ms 1.5 s) but 30 earned minutes divided by
  // 1 800 000 and came out 0 % — an empty chart with nothing to explain it. The thirty-minute floor is
  // the smallest thing this feature trades in: one correct group. Without it, a week whose only bar is
  // two minutes of watching would draw it as tall as an hour.
  const drawn = d => (anyUsed ? (d.used_ms || 0) : (d.awarded_ms || 0));
  const peak = Math.max(30 * 60_000, ...days.map(drawn));

  const cols = days.map(d => {
    const outer = drawn(d);
    const inner = Math.min(d.awarded_ms || 0, outer);
    // Anything that happened at all gets a visible stub; a 2 %-of-a-week day is still not nothing.
    const h = outer > 0 ? Math.max(3, Math.round((outer / peak) * 100)) : 0;
    const ih = outer ? Math.round((inner / outer) * 100) : 0;
    return `<div class="col" title="${esc(d.day_key)}">
      <div class="stem-bar" style="height:${h}%"><i style="height:${ih}%"></i></div></div>`;
  }).join('');

  const axis = days.map(d => {
    const date = new Date(`${d.day_key}T12:00:00`);
    const name = date.toLocaleDateString(LOCALE[lang], { weekday: 'short' });
    return `<span>${esc(name)}</span>`;
  }).join('');

  const totals = [
    [wk.attempts, fill(wk.times, { n: data.attempts })],
    [wk.passed, fill(wk.times, { n: data.passed })],
    [wk.rate, data.pass_rate == null ? '—' : `${Math.round(data.pass_rate * 100)}%`],
    [wk.earned, fmtDuration(data.awarded_ms)],
  ].map(([k, v]) => `<div class="row"><span class="k">${esc(k)}</span><span class="v">${esc(v)}</span></div>`).join('');

  const worst = data.worst_topics.length
    ? data.worst_topics.map(t => `<div class="row"><span class="k">${esc(t.topic)}</span>
        <span class="v dim">${esc(fill(wk.times, { n: t.wrong }))}</span></div>`).join('')
    : `<p class="footer-note" style="margin:10px 0 0">${esc(wk.none)}</p>`;

  // Deep is what quizzes earned, pale is the rest of the watching. When the TV has not reported any
  // viewing time yet (no heartbeat), there is no "rest" to show, so the legend drops that entry rather
  // than inventing a colour that means nothing.
  const legend = anyUsed
    ? `<div class="legend"><span><i style="background:var(--tint)"></i>${esc(wk.legendEarned)}</span>
        <span><i style="background:var(--tint-soft)"></i>${esc(wk.legendUsed)}</span></div>`
    : `<div class="legend"><span><i style="background:var(--tint)"></i>${esc(wk.legendEarned)}</span></div>`;

  return `<div class="card"><h2>${esc(wk.chart)}</h2>
      <div class="chart">${cols}</div><div class="axis">${axis}</div>${legend}</div>
    <div class="card"><div class="kv">${totals}</div></div>
    <div class="card"><h2>${esc(wk.worst)}</h2><div class="kv">${worst}</div></div>`;
}

// ---------------------------------------------------------------- routing

async function load(name) {
  const day = dayKey();
  const tv = deviceId ? `device_id=${encodeURIComponent(deviceId)}` : '';
  if (name === 'today') return api(`/v1/h5/summary?day_key=${day}&${tv}`);
  if (name === 'week') return api(`/v1/h5/report?day_key=${day}&${tv}`);
  if (name === 'wrong') return api(`/v1/h5/wrongbook?day_key=${day}&days=7&${tv}`);
  if (name === 'settings') return api(`/v1/h5/config?${tv}`);
  return null;
}

async function go(name) {
  view = name;
  $$('.tab').forEach(tab => tab.classList.toggle('is-on', tab.dataset.tab === name));
  const host = $('#view');
  host.innerHTML = skeleton();
  try {
    const data = await load(name);
    cache[name] = data;
    if (name === 'settings') {
      host.innerHTML = renderSettings(data.config);
      bindSettings(data);
    } else if (name === 'today') {
      setDeviceName(data);
      host.innerHTML = renderToday(data);
    } else if (name === 'week') {
      host.innerHTML = renderWeek(data);
    } else {
      host.innerHTML = renderWrong(data);
    }
  } catch (err) {
    if (signedOut(err)) return;
    if (err.code === 'not-yours') {
      // Somebody removed this phone from the TV (or the TV was replaced). Re-read the account and
      // land on whatever is left rather than showing a red card about it.
      await refreshMe();
      if (!me || !me.devices.length) { showPair(); return; }
      pickDevice();
      await go(name);
      return;
    }
    host.innerHTML = `<div class="card"><div class="empty"><strong>${esc(T().common.error)}</strong>
      <button class="primary" id="retry" style="margin-top:18px;max-width:180px">${esc(T().common.retry)}</button></div></div>`;
    $('#retry').addEventListener('click', () => go(name));
  }
}

function refresh() {
  if ($('#screen-main').hidden) return;
  // Never redraw the settings page from under a thumb: a language switch would reset a stepper the
  // parent is halfway through. It catches up the next time the tab is opened.
  if (view === 'settings') return;
  go(view);
}

// ---------------------------------------------------------------- settings

let saveTimer = null;
let draft = null;

function bindSettings(data) {
  draft = { ...data.config };

  $$('[data-step]').forEach(button => {
    button.addEventListener('click', () => {
      const key = button.dataset.step;
      const delta = Number(button.dataset.delta);
      const next = nextSetting(key, draft[key], delta);
      if (next < MIN[key] || next > MAX[key]) return;
      draft[key] = next;
      // Redraw just this row's value and the disabled state of its two buttons.
      const stepper = button.parentElement;
      stepper.querySelector('.val').textContent = fmtMinutes(next);
      stepper.querySelector('[data-delta="-1"]').disabled = nextSetting(key, next, -1) < MIN[key];
      stepper.querySelector('[data-delta="1"]').disabled = nextSetting(key, next, 1) > MAX[key];
      queueSave();
    });
  });

  $('#setq')?.addEventListener('click', () => showAuth('question'));
  $('#change')?.addEventListener('click', () => showAuth('change'));

  $('#unpair')?.addEventListener('click', async () => {
    if (!confirm(T().settings.unpairConfirm)) return;
    try {
      await api('/v1/h5/unpair', { method: 'POST', body: { device_id: deviceId } });
    } finally {
      // The account stays signed in; what changes is which televisions it holds. With none left the
      // page goes back to the pairing screen, which is where the next code belongs.
      await refreshMe();
      Object.keys(cache).forEach(key => delete cache[key]);
      if (!me || !me.devices.length) {
        deviceId = null;
        localStorage.removeItem('aq.device');
        showPair();
      } else {
        pickDevice();
        await go('today');
      }
    }
  });
}

function queueSave() {
  clearTimeout(saveTimer);
  saveTimer = setTimeout(async () => {
    try {
      const result = await api(`/v1/h5/config?device_id=${encodeURIComponent(deviceId)}`,
        { method: 'PUT', body: { config: draft } });
      cache.settings = { version: result.version, config: result.config, updated_at: null };
      toast(T().settings.saved);
    } catch (err) {
      if (signedOut(err)) return;
      toast(T().common.error);
    }
  }, 420);
}

// ---------------------------------------------------------------- boot

function detectLanguage() {
  const stored = localStorage.getItem('aq.lang');
  if (stored && LANGS.includes(stored)) return stored;
  const wanted = (navigator.languages && navigator.languages[0]) || navigator.language || 'en';
  const base = wanted.toLowerCase().split('-')[0];
  return LANGS.includes(base) ? base : 'en';
}

async function boot() {
  lang = detectLanguage();
  applyLanguage();

  $('#pair-form').addEventListener('submit', submitPair);
  $('#pair-code').addEventListener('input', event => {
    // Strip nothing: the code is case-sensitive and every character is significant. Only whitespace
    // is removed, because keyboards like to add it.
    event.target.value = event.target.value.replace(/\s+/g, '');
    $('#pair-error').hidden = true;
  });

  $('#lang').addEventListener('click', () => {
    lang = LANGS[(LANGS.indexOf(lang) + 1) % LANGS.length];
    localStorage.setItem('aq.lang', lang);
    applyLanguage();
  });

  $$('.tab').forEach(tab => tab.addEventListener('click', () => {
    if (tab.dataset.tab === view && cache[tab.dataset.tab]) return;
    go(tab.dataset.tab);
  }));

  $('#auth-form').addEventListener('submit', submitAuth);
  $('#auth-switch').addEventListener('click', event => {
    event.preventDefault();
    showAuth(authMode === 'up' ? 'in' : 'up');
  });
  $('#auth-forgot').addEventListener('click', event => {
    event.preventDefault();
    recoverEmail = null;
    recoverQuestion = null;
    showAuth('recover');
  });
  // The rules light up while typing, and the error from the last attempt stops applying.
  ['#auth-password', '#auth-password2', '#auth-email'].forEach(id => {
    $(id).addEventListener('input', () => {
      paintRules();
      $('#auth-error').hidden = true;
    });
  });
  $('#device-switch').addEventListener('click', openSheet);
  $('#sheet-close').addEventListener('click', closeSheet);
  $('#sheet-backdrop').addEventListener('click', event => {
    // Only the backdrop closes it; a tap inside the sheet must not.
    if (event.target === $('#sheet-backdrop')) closeSheet();
  });

  // Who is holding this phone decides which screen opens. `refreshMe` puts up the sign-in screen or
  // the claim form itself when there is no session, so there is nothing more to do in that case.
  if (!await refreshMe()) return;
  await enter();
}

document.addEventListener('DOMContentLoaded', boot);
