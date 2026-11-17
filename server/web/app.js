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
      needQuestion: '问题至少 3 个字，写一个只有你知道答案的。', needAnswer: '答案至少 2 个字——忘记密码时就靠它。',
      ruleQuestion: '问题至少 3 个字', ruleAnswer: '答案至少 2 个字',
      currentQuestionLabel: '现在的问题', oldAnswerLabel: '答案', needOldAnswer: '先答对现在的问题，才能换成新的。',
      newQuestionLabel: '新的问题', newAnswerLabel: '新的答案',
      badAnswer: '答案不对。', recoverLocked: '答错次数太多，过 15 分钟再试。',
      noAccount: '这个邮箱没有注册过。',
      noQuestion: '这个账号没有设问题，用不了这种方式。',
      samePassword: '这就是现在用的密码。', weakPassword: '密码不能只有数字。',
      changed: '密码已改，请用新密码登录。', resetDone: '密码已重设，用新密码登录。',
      setQuestionTitle: '找回问题', hintSetQuestion: '忘记密码时靠它回来。改掉之后，旧的答案就作废了。',
      hintSetQuestionFirst: '换问题前要先答对现在的问题——这是唯一能把账号找回来的凭据。',
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
      questionSet: '已设置', questionUnset: '没有设置',
      language: '语言',
      progress: '孩子学到哪儿',
      progressNote: '出哪些单元的题由进度决定。填一次开学日期，之后按周往前推。',
      startDate: '开学第一天',
      startHint: '填这学期第一个周一。放假缺的周不用管，差一两周可以单独指定某一科。',
      weekN: '第 {n} 周',
      unitN: '第 {n} 单元',
      reviewing: '学完了',
      autoByDate: '按日期',
      setByParent: '家长指定',
      unitPick: '现在学到',
      progressSaved: '已保存',
      badDate: '日期没填完整。',
    },
    wrong: {
      empty: '最近没有错题', emptyHint: '做错的题会出现在这里，带着正确答案和解析。',
      yours: '他答', right: '答案', days: '最近 {n} 天',
      flagged: '标记没学过',

      weak: '薄弱知识点', weakAll: '全部 {n} 个 ›', weakEmpty: '还没有薄弱知识点',
      weakEmptyHint: '孩子在电视上做错的题会自动折算成知识点的分数，出现在这里。',
      need: '再答对 {n} 次', needDone: '已回到正常', score: '{n} 分',
      word0: '牢固', word1: '需巩固', word2: '不牢', word3: '很弱',

      teach: '本周该讲', teachNote: '一周讲掉 1–2 个就够，不用全清。',
      arrived: '新增', cleared: '已清掉', avg: '平均掌握度', weekK: '近 7 天',
      avgMove: '{a} 分 → {b} 分',
      listNote: '分数越高越不牢固。做对一次 −10，连着做对两次再多 −10；扣到 11 分以下就不再单独练。',

      list: '错题清单', listAll: '全部 {n} 条 ›', listEmpty: '最近没有错题',
      listHint: '电视上做错的、你手动录的，都在这里。',
      filterAll: '全部', filterTv: '电视', filterManual: '手动录入',
      srcTv: '电视', srcManual: '手动录入', noAnswer: '只记了知识点，没有题目',
      more: '再看 {n} 条', total: '最近 {n} 天 · 共 {m} 条',
      answers: '答案与解析只给家长看，孩子端不显示。',

      detailWeight: '出题权重', detailTarget: '目标题量', detailBank: '题库已有',
      detailToday: '今天已出', capLimit: '{n} 题（上限 {m}）',
      times: '{n} 倍', qty: '{n} 题',
      flow: '加减分记录', flowNote: '每一次加减分都能回放，核得到具体是哪一道题。',
      flowEmpty: '还没有记录。',
      coach: '可以这样跟他说', coachWhy: '他为什么会问', coachAvoid: '别这么说',
      topicWrong: '这个知识点的错题',

      adjust: '手动调整', adjustWhy: '写一句为什么，会记进流水',
      adjustDown: '−10 已经会了', adjustUp: '＋10 还是不牢',
      adjustSaved: '已调整',

      add: '添加错题', addTitle: '添加错题', addCount: '错了几道',
      addSave: '保存', addNote: '拍照识别还在做：选一个知识点、填错了几个，20 秒录完。',
      countN: '{n} 道', addDone: '记下了，下一组就会练它。',
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
      needQuestion: 'At least three characters — a question only you can answer.', needAnswer: 'At least two characters — this is the only way back in.',
      ruleQuestion: 'Three characters or more', ruleAnswer: 'Two characters or more',
      currentQuestionLabel: 'The question now', oldAnswerLabel: 'Your answer',
      newQuestionLabel: 'The new question', newAnswerLabel: 'The new answer',
      needOldAnswer: 'Answer the current question first, then you can replace it.',
      badAnswer: 'That is not the answer.', recoverLocked: 'Too many wrong answers. Try again in fifteen minutes.',
      noAccount: 'No account with that email.',
      noQuestion: 'This account has no question, so this route does not apply.',
      samePassword: 'That is the password you already have.', weakPassword: 'Not digits only.',
      changed: 'Password changed. Sign in with the new one.', resetDone: 'Password reset. Sign in with the new one.',
      setQuestionTitle: 'Recovery question', hintSetQuestion: 'This is how you get back in. Replacing it retires the old answer.',
      hintSetQuestionFirst: 'Answer the current question first — it is the only way back into this account.',
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
      questionSet: 'Set', questionUnset: 'Not set',
      language: 'Language',
      progress: 'Where the class is',
      progressNote: 'Progress decides which units the questions come from. Enter the term once; the weeks follow.',
      startDate: 'First day of term',
      startHint: 'The first Monday of this term. Skip holiday weeks; a subject that is off by a week can be set on its own.',
      weekN: 'Week {n}',
      unitN: 'Unit {n}',
      reviewing: 'Book finished',
      autoByDate: 'from the date',
      setByParent: 'set by hand',
      unitPick: 'Now on',
      progressSaved: 'Saved',
      badDate: 'That date is incomplete.',
    },
    wrong: {
      empty: 'No mistakes recently', emptyHint: 'Wrong answers show up here with the right answer and the explanation.',
      yours: 'They said', right: 'Answer', days: 'Last {n} days',
      flagged: 'Marked as not learned',

      weak: 'Topics to work on', weakAll: 'All {n} ›', weakEmpty: 'Nothing to work on',
      weakEmptyHint: 'A wrong answer on the TV becomes a score for that topic, and it shows up here.',
      need: '{n} more right', needDone: 'Back to normal', score: '{n}',
      word0: 'Solid', word1: 'Shaky', word2: 'Weak', word3: 'Weakest',

      teach: 'Teach this week', teachNote: 'One or two a week is enough. There is no need to clear them all.',
      arrived: 'New', cleared: 'Cleared', avg: 'Average score', weekK: 'Last 7 days',
      avgMove: '{a} → {b}',
      listNote: 'A higher score means less solid. One right answer is −10; two in a row is another −10. Below 12 it stops being practised on its own.',

      list: 'Past mistakes', listAll: 'All {n} ›', listEmpty: 'No mistakes recently',
      listHint: 'Wrong on the TV, or recorded here by hand — all of it lives in one list.',
      filterAll: 'All', filterTv: 'TV', filterManual: 'By hand',
      srcTv: 'TV', srcManual: 'By hand', noAnswer: 'Topic recorded, no question',
      more: '{n} more', total: 'Last {n} days · {m} in total',
      answers: 'Answers and explanations are for the parent. The TV never shows them.',

      detailWeight: 'Weight', detailTarget: 'Target questions', detailBank: 'In the bank',
      detailToday: 'Asked today', capLimit: '{n} (cap {m})',
      times: '{n}×', qty: '{n}',
      flow: 'How the score moved', flowNote: 'Every change is replayable — you can trace it to the question.',
      flowEmpty: 'Nothing yet.',
      coach: 'What you can say to them', coachWhy: 'Why they bring it up', coachAvoid: 'Not this',
      topicWrong: 'Mistakes on this topic',

      adjust: 'Adjust by hand', adjustWhy: 'Say why — it goes into the record',
      adjustDown: '−10 they know it', adjustUp: '+10 still shaky',
      adjustSaved: 'Adjusted',

      add: 'Add a mistake', addTitle: 'Add a mistake', addCount: 'How many wrong',
      addSave: 'Save', addNote: 'Photo recognition is still being built: pick a topic, say how many, and it takes 20 seconds.',
      countN: '{n}', addDone: 'Recorded. It will be in the next group.',
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
      needQuestion: 'Не короче 3 символов — вопрос, ответ на который знаете только вы.', needAnswer: 'Не короче 2 символов — на нём всё держится.',
      ruleQuestion: 'Не короче 3 символов', ruleAnswer: 'Не короче 2 символов',
      currentQuestionLabel: 'Текущий вопрос', oldAnswerLabel: 'Ваш ответ',
      newQuestionLabel: 'Новый вопрос', newAnswerLabel: 'Новый ответ',
      needOldAnswer: 'Сначала ответьте на текущий вопрос, потом можно менять.',
      badAnswer: 'Ответ неверный.', recoverLocked: 'Слишком много неверных ответов. Через пятнадцать минут.',
      noAccount: 'Такая почта не зарегистрирована.',
      noQuestion: 'У этого аккаунта нет вопроса, этот способ не подойдёт.',
      samePassword: 'Это и есть текущий пароль.', weakPassword: 'Не только цифры.',
      changed: 'Пароль изменён. Войдите с новым.', resetDone: 'Пароль сброшен. Войдите с новым.',
      setQuestionTitle: 'Вопрос для восстановления', hintSetQuestion: 'По нему вы вернётесь в аккаунт. Замените — старый ответ перестанет работать.',
      hintSetQuestionFirst: 'Сначала ответьте на текущий вопрос — это единственный путь назад в аккаунт.',
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
      questionSet: 'Задан', questionUnset: 'Не задан',
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
      needQuestion: 'Не коротше 3 символів — питання, відповідь на яке знаєте тільки ви.', needAnswer: 'Не коротше 2 символів — на ній усе тримається.',
      ruleQuestion: 'Не коротше 3 символів', ruleAnswer: 'Не коротше 2 символів',
      currentQuestionLabel: 'Поточне питання', oldAnswerLabel: 'Ваша відповідь',
      newQuestionLabel: 'Нове питання', newAnswerLabel: 'Нова відповідь',
      needOldAnswer: 'Спершу дайте відповідь на поточне питання, потім можна змінювати.',
      badAnswer: 'Відповідь неправильна.', recoverLocked: 'Забагато неправильних відповідей. За пʼятнадцять хвилин.',
      noAccount: 'Така пошта не зареєстрована.',
      noQuestion: 'У цього запису немає питання, цей спосіб не підійде.',
      samePassword: 'Це і є поточний пароль.', weakPassword: 'Не лише цифри.',
      changed: 'Пароль змінено. Увійдіть із новим.', resetDone: 'Пароль скинуто. Увійдіть із новим.',
      setQuestionTitle: 'Питання для відновлення', hintSetQuestion: 'За ним ви повернетеся до запису. Заміните — стара відповідь перестане діяти.',
      hintSetQuestionFirst: 'Спершу дайте відповідь на поточне питання — це єдиний шлях назад до запису.',
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
      questionSet: 'Задано', questionUnset: 'Не задано',
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
      needQuestion: 'Кемінде 3 таңба — жауабын тек сіз білетін сұрақ.', needAnswer: 'Кемінде 2 таңба — бәрі соған тіреледі.',
      ruleQuestion: 'Кемінде 3 таңба', ruleAnswer: 'Кемінде 2 таңба',
      currentQuestionLabel: 'Қазіргі сұрақ', oldAnswerLabel: 'Жауабыңыз',
      newQuestionLabel: 'Жаңа сұрақ', newAnswerLabel: 'Жаңа жауап',
      needOldAnswer: 'Алдымен қазіргі сұраққа жауап беріңіз, содан кейін ауыстыруға болады.',
      badAnswer: 'Жауап қате.', recoverLocked: 'Қате жауап тым көп. Он бес минуттан кейін.',
      noAccount: 'Бұл пошта тіркелмеген.',
      noQuestion: 'Бұл аккаунтта сұрақ жоқ, бұл жол келмейді.',
      samePassword: 'Бұл қазіргі құпиясөз.', weakPassword: 'Тек цифр емес.',
      changed: 'Құпиясөз ауыстырылды. Жаңасымен кіріңіз.', resetDone: 'Құпиясөз қалпына келтірілді. Жаңасымен кіріңіз.',
      setQuestionTitle: 'Қалпына келтіру сұрағы', hintSetQuestion: 'Осы арқылы қайтасыз. Ауыстырсаңыз, ескі жауап күшін жояды.',
      hintSetQuestionFirst: 'Алдымен қазіргі сұраққа жауап беріңіз — бұл аккаунтқа қайтудың жалғыз жолы.',
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
      questionSet: 'Орнатылған', questionUnset: 'Орнатылмаған',
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

/** The copy for the current language, with Chinese filling any gap.
 *
 *  The wrong-book pages landed in Chinese and English first, because that copy is read by a parent and
 *  translating it badly is worse than not translating it. The other three languages keep their own
 *  existing strings and borrow Chinese for the new ones — better than a blank label or a half-and-half
 *  sentence nobody can proofread. */
let textsFor = { lang: null, value: null };
function T() {
  if (textsFor.lang === lang) return textsFor.value;
  let value = TEXTS[lang] || TEXTS.zh;
  if (lang !== 'zh') {
    value = Object.fromEntries(Object.keys(TEXTS.zh).map(group => [
      group, { ...TEXTS.zh[group], ...((TEXTS[lang] || {})[group] || {}) },
    ]));
  }
  textsFor = { lang, value };
  return value;
}

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
  // Replacing a question asks for the old answer first, and the hint has to say so — otherwise the
  // extra field reads like the page doubting the parent rather than protecting the account.
  // The text of the question, not just whether there is one: the replace screen has to print it,
  // because "answer the current question" with no question on screen asks the parent to remember
  // two things at once.
  const oldQuestion = (me && me.account && me.account.question) || '';
  const hasQuestion = !!oldQuestion;
  if (authMode === 'question' && hasQuestion) hints.question = a.hintSetQuestionFirst;

  // The two fields are the *new* pair when there is an old one above them; leaving them labelled
  // "找回密码的问题" under a filled-in "现在这个问题的答案" reads like the same question twice.
  const relabel = (id, key, value) => {
    const node = $(id);
    if (!node) return;
    node.dataset.i18n = `auth.${key}`;
    node.textContent = value;
  };
  const replacing = authMode === 'question' && hasQuestion;
  relabel('#auth-question-label', replacing ? 'newQuestionLabel' : 'questionLabel',
    replacing ? a.newQuestionLabel : a.questionLabel);
  relabel('#auth-answer-label', replacing ? 'newAnswerLabel' : 'answerLabel',
    replacing ? a.newAnswerLabel : a.answerLabel);
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
    '#auth-old', '#auth-old-answer'].forEach(id => { $(id).value = ''; });
  $('#auth-error').hidden = true;
  applyAuthMode();
  // After applyAuthMode: that call shows every field whose `data-modes` names this screen, and the
  // old-answer field is only wanted when there is a question to replace.
  $('#auth-old-wrap').hidden = !(authMode === 'question' && hasQuestion);
  $('#auth-old-q-wrap').hidden = !(authMode === 'question' && hasQuestion);
  if (hasQuestion) $('#auth-old-question').textContent = oldQuestion;
  if (authMode === 'in' || authMode === 'change' || authMode === 'recover2'
      || authMode === 'question') {
    challenge = null;                      // nothing is being registered, so no nonce to fetch
  } else {
    startAuth();
  }
  paintRules();
  // Typing the old answer is the first thing the screen asks for when one is required, so the cursor
  // starts there rather than on the new question.
  const first = authMode === 'change' ? '#auth-old'
    : authMode === 'question' ? (hasQuestion ? '#auth-old-answer' : '#auth-question-input')
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
  // The question and the answer are measured the way `normalise_question()` / `normalise_answer()`
  // measure them: trimmed and with runs of spaces collapsed, so "红色" and " 红  色 " differ here
  // exactly as they differ on the server (QUESTION_MIN 3, ANSWER_MIN 2).
  const squeeze = text => (text || '').trim().replace(/\s+/g, ' ');
  return {
    length: password.length >= 8,
    digits: password.length > 0 && !/^\d+$/.test(password),
    notemail: password.length > 0 && !(local && flat === local),
    common: password.length > 0 && !WEAK_LOCAL.includes(flat),
    match: again.length > 0 && password === again,
    qLength: squeeze($('#auth-question-input') && $('#auth-question-input').value).length >= 3,
    aLength: squeeze($('#auth-answer') && $('#auth-answer').value).length >= 2,
  };
}

function paintRules() {
  const state = ruleState();
  // Two lists now (passwords, the question pair); neither is on screen at the same time.
  $$('#auth-form .rules').forEach(box => {
    if (box.hidden) return;
    box.querySelectorAll('li').forEach(item => {
      item.classList.toggle('is-ok', !!state[item.dataset.rule]);
    });
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
    case 'need-old-answer': return a.needOldAnswer;
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
    // Sent only when there is a question to replace; the server refuses a replacement without it.
    if (!$('#auth-old-wrap').hidden) body.old_answer = $('#auth-old-answer').value;
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
  // The empty answer for `recover2` is refused in `checkLocally`, alongside the other local checks;
  // it used to be repeated here against a variable that does not exist on this screen.
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
      // The account has one now, and the settings row and the next visit to this screen both read
      // that flag — without this line the old-answer field would stay hidden until a reload.
      if (me && me.account) {
        me.account.has_question = true;
        me.account.question = result.question || '';
      }
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
    // Same order the server checks in: the old answer (when there is a question to replace), then the
    // new pair. Length limits are checked here too — a one-character answer used to travel to the
    // server and come back as a sentence that never mentioned length.
    if (!$('#auth-old-wrap').hidden && !$('#auth-old-answer').value.trim()) return a.needOldAnswer;
    const state = ruleState();
    if (!state.qLength) return a.needQuestion;
    if (!state.aLength) return a.needAnswer;
  }
  if (authMode === 'recover2' && !$('#auth-answer2').value.trim()) return a.needAnswer;
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
  // Whether the account has a question yet: the row says so, because "not set" is the state that
  // makes a forgotten password unrecoverable, and nothing else on this page hints at it.
  const hasQuestion = !!(me && me.account && me.account.has_question);
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
      <div class="row tap" id="progress"><span class="k">${esc(s.progress)}</span>
        <span class="v dim">›</span></div>
      <div class="row tap" id="setq"><span class="k">${esc(s.setQuestion)}</span>
        <span class="v dim">${esc(hasQuestion ? s.questionSet : s.questionUnset)} ›</span></div>
      <div class="row tap" id="change"><span class="k">${esc(s.change)}</span>
        <span class="v dim">›</span></div>
      <div class="row tap" id="unpair"><span class="k" style="color:var(--bad)">${esc(s.unpair)}</span>
        <span class="v dim">›</span></div>
    </div></div>`;
}

// ---------------------------------------------------------------- the wrong tab, five pages
//
// Overview → weak list → one topic → past mistakes → the add sheet. The tab used to be one flat list;
// it is now the only place in the product where a score is visible (the TV says nothing, §10), so the
// overview has to answer "how many holes" and "what did he get wrong" before the parent scrolls, and
// the detail page has to answer "why 72" without a paragraph of theory.

/** Which sub-page is open inside the wrong tab: null = overview. */
let page = null;
let pageArg = null;
/** The list's filter and scroll position, kept while moving between pages. */
let listSource = 'all';
let listOffset = 0;
const LIST_PAGE = 40;
/** The add sheet's draft: the subject tab, the chosen topic, the count, and the batch id that makes a
 *  second tap harmless. */
let addDraft = null;

const qs = (extra = '') => {
  const day = `day_key=${dayKey()}`;
  const tv = deviceId ? `&device_id=${encodeURIComponent(deviceId)}` : '';
  return `?${day}${tv}${extra}`;
};

const levelWord = level => T().wrong[`word${level}`] || '';
const levelClass = level => (level >= 3 ? 'bad' : level === 2 ? 'l2' : level === 1 ? 'l1' : '');

function subhead(title) {
  const back = T().tab[view] || '';
  return `<div class="subhead"><button type="button" id="back" aria-label="${esc(back)}">‹ ${esc(back)}</button>
    <h1>${esc(title)}</h1></div>`;
}

/** One topic on the weak list: name, how many rights are left, its unit, the score, and a bar.
 *  The bar is the score itself, not progress towards anything — 72 means 72. */
function weakRow(item) {
  const w = T().wrong;
  return `<div class="mrow" data-topic="${item.topic_id}">
    <div class="top"><span class="nm">${esc(item.topic)}</span>
      <span class="need${item.need_right ? '' : ' done'}">${esc(
        item.need_right ? fill(w.need, { n: item.need_right }) : w.needDone)}</span></div>
    <div class="sub2"><span class="pill ${levelClass(item.level)}">${esc(levelWord(item.level))}</span>
      <span class="u">${esc(item.unit)}</span>
      <span class="sc">${esc(fill(w.score, { n: item.score }))}</span></div>
    <div class="mbar l${item.level}"><i style="width:${item.score}%"></i></div>
  </div>`;
}

function qcard(item) {
  const w = T().wrong;
  const tags = [
    `<span class="src ${item.source === 'paper' ? 'man' : 'tv'}">${esc(
      item.source === 'paper' ? w.srcManual : w.srcTv)}</span>`,
    item.subject ? `<span class="pill">${esc(item.subject)}</span>` : '',
    item.topic ? `<span class="pill">${esc(item.topic)}</span>` : '',
    item.flagged_unknown ? `<span class="pill">${esc(w.flagged)}</span>` : '',
  ].join('');
  const yours = item.chosen_text || item.chosen || '';
  const right = item.answer_text || '';
  // A recorded mistake has no question behind it: an empty "he said / answer" pair would look like the
  // child answered nothing, so it says what actually happened instead.
  const answers = item.source === 'paper' && !item.stem
    ? `<p class="meta" style="margin:0">${esc(w.noAnswer)}</p>`
    : `<div class="answer">
        <div class="line yours"><b>${esc(w.yours)}</b><span>${esc(yours || '—')}</span></div>
        <div class="line right"><b>${esc(w.right)}</b><span>${esc(right || '—')}</span></div>
      </div>`;
  const stem = item.stem ? `<p class="stem">${esc(item.stem)}</p>` : '';
  const why = item.explanation ? `<p class="why">${esc(item.explanation)}</p>` : '';
  return `<div class="q">
    <div class="head">${tags}</div>
    ${stem}
    ${answers}
    ${why}
    <p class="meta">${esc(item.day_key)}${item.unit ? ' · ' + esc(item.unit) : ''}</p>
  </div>`;
}

function renderWeakOverview(mastery, wrong) {
  const w = T().wrong;
  const top = mastery.items.slice(0, 2);
  const weakCard = top.length
    ? `<div class="sec-in"><h2>${esc(w.weak)}</h2>
         <a href="#" data-go="topics">${esc(fill(w.weakAll, { n: mastery.count }))}</a></div>
       <p class="sub">${esc(mastery.week_line)}</p>
       ${top.map(weakRow).join('')}`
    : `<div class="sec-in"><h2>${esc(w.weak)}</h2></div>
       <div class="empty"><strong>${esc(w.weakEmpty)}</strong>${esc(w.weakEmptyHint)}</div>`;

  const recent = (wrong.items || []).slice(0, 2);
  const listHead = `<div class="sec"><h2>${esc(w.list)}</h2>${
    wrong.total ? `<a href="#" data-go="list">${esc(fill(w.listAll, { n: wrong.total }))}</a>` : ''}</div>`;
  const listBody = recent.length
    ? recent.map(qcard).join('')
    : `<div class="card"><div class="empty"><strong>${esc(w.listEmpty)}</strong>${esc(w.listHint)}</div></div>`;

  return `<div class="card">${weakCard}</div>${listHead}${listBody}
    <div class="dock"><button class="primary" type="button" id="add-open">${esc(w.add)}</button></div>`;
}

function renderTopics(mastery) {
  const w = T().wrong;
  const teach = mastery.items.slice(0, 3);
  const week = mastery.week || {};
  const rows = (items, withIndex) => items.map((item, index) => `<div class="row" data-topic="${item.topic_id}">
      <span class="k">${withIndex ? `${index + 1}. ` : ''}${esc(item.topic)}</span>
      <span class="v dim">${esc(levelWord(item.level))} · ${esc(fill(w.score, { n: item.score }))}</span>
    </div>`).join('');

  const teachCard = teach.length
    ? `<p class="sub">${esc(w.teachNote)}</p><div class="kv">${rows(teach, true)}</div>`
    : `<div class="empty"><strong>${esc(w.weakEmpty)}</strong>${esc(w.weakEmptyHint)}</div>`;

  return `${subhead(w.weak)}
    <div class="card"><h2>${esc(w.teach)}</h2>${teachCard}</div>
    <div class="card"><h2>${esc(fill(w.weakAll, { n: mastery.count }).replace(' ›', ''))}</h2>
      <div class="kv">${mastery.items.map(item => weakRow(item)).join('')}</div></div>
    <div class="card"><h2>${esc(w.weekK)}</h2><div class="kv">
      <div class="row"><span class="k">${esc(w.arrived)}</span><span class="v">${week.arrived ?? 0}</span></div>
      <div class="row"><span class="k">${esc(w.cleared)}</span><span class="v">${week.cleared ?? 0}</span></div>
      <div class="row"><span class="k">${esc(w.avg)}</span>
        <span class="v">${esc(fill(w.avgMove, { a: week.avg_before ?? 0, b: week.avg_now ?? 0 }))}</span></div>
    </div></div>
    <p class="footer-note">${esc(w.listNote)}</p>`;
}

function renderTopicDetail(t) {
  const w = T().wrong;
  const flow = t.events.length
    ? t.events.map(event => `<div class="f">
        <span class="when">${esc(String(event.at).slice(5, 16))}</span>
        <span class="what">${esc(event.label)}</span>
        <span class="d ${event.delta > 0 ? 'up' : 'dn'}">${event.delta > 0 ? '+' : '−'}${Math.abs(event.delta)}</span>
      </div>`).join('')
    : `<p class="footer-note" style="margin:0">${esc(w.flowEmpty)}</p>`;

  const numbers = [
    [w.detailWeight, fill(w.times, { n: t.weight })],
    [w.detailTarget, fill(w.qty, { n: t.target_qty })],
    [w.detailBank, fill(w.qty, { n: t.in_bank })],
    [w.detailToday, fill(w.capLimit, { n: t.today_used, m: t.daily_cap })],
  ].map(([k, v]) => `<div class="row"><span class="k">${esc(k)}</span>
      <span class="v">${esc(v)}</span></div>`).join('');

  const wrongCards = t.wrong_items.length
    ? t.wrong_items.map(item => `<div class="row" style="align-items:flex-start">
        <span class="k" style="font-size:14px">${esc(item.stem || w.noAnswer)}</span>
        <span class="v dim">${esc(item.day_key.slice(5))}</span></div>`).join('')
    : `<p class="footer-note" style="margin:0">${esc(w.listEmpty)}</p>`;

  return `${subhead(t.topic)}
    <div class="card">
      <div class="hero"><span class="score">${t.score}</span></div>
      <div class="heroline"><span class="pill ${levelClass(t.level)}">${esc(levelWord(t.level))}</span>
        <span>${esc(t.need_right ? fill(w.need, { n: t.need_right }) : w.needDone)}</span></div>
      <div class="mbar l${t.level}" style="margin-top:14px"><i style="width:${t.score}%"></i></div>
    </div>
    <div class="card"><div class="kv">${numbers}</div></div>
    <div class="card"><h2>${esc(w.flow)}</h2><div class="flow">${flow}</div></div>
    <p class="footer-note">${esc(w.flowNote)}</p>
    <div class="card"><h2>${esc(w.coach)}</h2>
      <p class="say">「${esc(t.coach.say)}」</p>
      <div class="row"><span class="k">${esc(w.coachWhy)}</span></div>
      <p class="footer-note" style="margin:4px 0 12px">${esc(t.coach.why)}</p>
      <div class="row"><span class="k">${esc(w.coachAvoid)}</span></div>
      <p class="footer-note" style="margin:4px 0 0">${esc(t.coach.avoid)}</p>
    </div>
    <div class="card"><h2>${esc(w.topicWrong)}</h2><div class="kv">${wrongCards}</div></div>
    <div class="card">
      <button class="quiet" type="button" id="adjust-toggle" style="margin:0">${esc(w.adjust)}</button>
      <div class="adjust" id="adjust-form" hidden>
        <input id="adjust-note" type="text" maxlength="60" placeholder="${esc(w.adjustWhy)}"
               autocomplete="off" enterkeyhint="done">
        <div class="two">
          <button type="button" data-delta="-10">${esc(w.adjustDown)}</button>
          <button type="button" data-delta="10">${esc(w.adjustUp)}</button>
        </div>
      </div>
    </div>`;
}

function renderWrongList(data) {
  const w = T().wrong;
  const chips = [['all', w.filterAll], ['tv', w.filterTv], ['paper', w.filterManual]]
    .map(([key, label]) => `<button class="f ${data.source === key ? 'on' : ''}" type="button"
        data-source="${key}">${esc(label)}<b>${data.counts[key] ?? 0}</b></button>`).join('');
  const cards = data.items.length
    ? `<div id="cards">${data.items.map(qcard).join('')}</div>`
    : emptyState(w.listEmpty, w.listHint);
  const left = data.total - data.offset - data.items.length;
  const more = left > 0
    ? `<button class="quiet" type="button" id="more">${esc(fill(w.more, { n: left }))}</button>` : '';
  return `${subhead(w.list)}<div class="filters">${chips}</div>${cards}${more}
    <p class="footer-note">${esc(fill(w.total, { n: data.days, m: data.total }))} · ${esc(w.answers)}</p>`;
}

function renderProgress(data) {
  const s = T().settings;
  const books = data.books.map(book => {
    // The options are short on purpose: a <select> whose text is "第 5 单元 圆" pushes past the card
    // edge on a 390pt phone, and the unit's name is already on the line above.
    const options = book.units.map(unit => `<option value="${unit.ordinal}"
        ${unit.ordinal === book.current ? 'selected' : ''}>${esc(fill(s.unitN, { n: unit.ordinal }))}</option>`).join('');
    const unit = book.units.find(one => one.ordinal === book.current) || {};
    const note = book.reviewing ? s.reviewing : (book.source === 'parent' ? s.setByParent : s.autoByDate);
    return `<div class="row">
      <span class="k">${esc(book.subject)}<small>${esc(book.book)} · ${esc(unit.name || '')} · ${esc(note)}</small></span>
      <select data-book="${book.textbook_id}" aria-label="${esc(s.unitPick)}">${options}</select>
    </div>`;
  }).join('');

  return `${subhead(s.progress)}
    <div class="card"><h2>${esc(s.startDate)}</h2>
      <p class="sub">${esc(s.startHint)}</p>
      <input class="date-input" id="start-date" type="date" value="${esc(data.start_date || '')}">
      <p class="footer-note">${esc(data.week ? fill(s.weekN, { n: data.week }) : '')}</p>
    </div>
    <div class="card"><h2>${esc(s.unitPick)}</h2><div class="kv">${books}</div></div>
    <p class="footer-note">${esc(s.progressNote)}</p>`;
}

// ---- the add sheet ----

function newBatchId() {
  // Crockford-ish, 26 characters, matching `PAPER_ID` on the server. Its only job is to be unique per
  // sheet opening so a double tap lands on the same batch instead of adding twice.
  const alphabet = '0123456789ABCDEFGHJKMNPQRSTVWXYZ';
  let id = '';
  for (let i = 0; i < 26; i += 1) id += alphabet[Math.floor(Math.random() * alphabet.length)];
  return id;
}

function renderAddSheet() {
  const w = T().wrong;
  const subjects = [...new Set(addDraft.topics.map(topic => topic.subject_code))];
  const seg = subjects.map(code => {
    const name = (addDraft.topics.find(topic => topic.subject_code === code) || {}).subject || code;
    return `<button type="button" data-subject="${esc(code)}"
      class="${code === addDraft.subject ? 'on' : ''}">${esc(name)}</button>`;
  }).join('');
  const rows = addDraft.topics
    .filter(topic => topic.subject_code === addDraft.subject)
    .map(topic => `<button class="sheet-row ${addDraft.topic === topic.topic_id ? 'is-on' : ''}"
        type="button" data-pick="${topic.topic_id}">
        <strong>${esc(topic.topic)}</strong>
        <span>${addDraft.topic === topic.topic_id ? '✓' : esc(topic.unit)}</span></button>`).join('');

  $('#add-body').innerHTML = `
    <h2 class="plain" id="add-title">${esc(w.addTitle)}</h2>
    <div class="seg">${seg}</div>
    <div class="picker">${rows}</div>
    <div class="row" style="border-top:1px solid var(--sep);margin-top:6px">
      <span class="k">${esc(w.addCount)}</span>
      <span class="stepper">
        <button type="button" data-count="-1" ${addDraft.count <= 1 ? 'disabled' : ''}>−</button>
        <span class="val">${esc(fill(w.countN, { n: addDraft.count }))}</span>
        <button type="button" data-count="1" ${addDraft.count >= 10 ? 'disabled' : ''}>+</button>
      </span></div>
    <p class="footer-note">${esc(w.addNote)}</p>
    <button class="primary" id="add-save" type="button"
      ${addDraft.topic ? '' : 'disabled'}>${esc(w.addSave)}</button>`;

  $$('#add-body [data-subject]').forEach(button => button.addEventListener('click', () => {
    addDraft.subject = button.dataset.subject;
    renderAddSheet();
  }));
  $$('#add-body [data-pick]').forEach(button => button.addEventListener('click', () => {
    addDraft.topic = Number(button.dataset.pick);
    renderAddSheet();
  }));
  $$('#add-body [data-count]').forEach(button => button.addEventListener('click', () => {
    addDraft.count = Math.max(1, Math.min(10, addDraft.count + Number(button.dataset.count)));
    renderAddSheet();
  }));
  $('#add-save').addEventListener('click', saveAdd);
}

async function openAdd() {
  if (!addDraft || !addDraft.topics.length) {
    try {
      const data = await api(`/v1/h5/topics${qs()}`);
      addDraft = { topics: data.topics, subject: null, topic: null, count: 1, batch: newBatchId() };
      addDraft.subject = data.topics.length ? data.topics[0].subject_code : null;
    } catch (err) {
      if (signedOut(err)) return;
      toast(T().common.error);
      return;
    }
  }
  renderAddSheet();
  $('#add-backdrop').hidden = false;
}

function closeAdd() {
  $('#add-backdrop').hidden = true;
}

async function saveAdd() {
  if (!addDraft || !addDraft.topic) return;
  const button = $('#add-save');
  button.disabled = true;
  try {
    await api(`/v1/h5/paper${qs()}`, {
      method: 'POST',
      body: {
        topic_id: addDraft.topic,
        count: addDraft.count,
        day_key: dayKey(),
        // Sent so a retry on a flaky connection lands on the same batch rather than a second one.
        paper_id: addDraft.batch,
      },
    });
    closeAdd();
    addDraft = null;
    toast(T().wrong.addDone);
    await refresh();
  } catch (err) {
    button.disabled = false;
    if (signedOut(err)) return;
    toast(T().common.error);
  }
}

// ---- loading and moving between the pages ----

async function loadWeak() {
  return api(`/v1/h5/mastery${qs()}`);
}

async function loadWrong(days = 30, limit = 60, offset = 0, source = 'all') {
  return api(`/v1/h5/wrongbook${qs(`&days=${days}&limit=${limit}&offset=${offset}&source=${source}`)}`);
}

async function nav(next, arg = null) {
  page = next;
  pageArg = arg;
  const host = $('#view');
  host.classList.remove('has-dock');
  host.innerHTML = skeleton();
  try {
    if (next === 'topics') {
      host.innerHTML = renderTopics(await loadWeak());
      bindTopicLinks();
    } else if (next === 'topic') {
      const data = await api(`/v1/h5/mastery/${arg}${qs()}`);
      host.innerHTML = renderTopicDetail(data);
      bindTopicDetail(data);
    } else if (next === 'list') {
      const data = await loadWrong(30, LIST_PAGE, 0, listSource);
      listOffset = data.items.length;
      host.innerHTML = renderWrongList(data);
      bindList();
    } else if (next === 'progress') {
      host.innerHTML = renderProgress(await api(`/v1/h5/curriculum${qs()}`));
      bindProgress();
    }
    $('#back')?.addEventListener('click', () => go(view));
  } catch (err) {
    if (signedOut(err)) return;
    host.innerHTML = `<div class="card"><div class="empty"><strong>${esc(T().common.error)}</strong>
      <button class="primary" id="retry" style="margin-top:18px;max-width:180px">${esc(T().common.retry)}</button></div></div>`;
    $('#retry').addEventListener('click', () => nav(next, arg));
  }
}

function bindTopicLinks() {
  $$('#view [data-topic]').forEach(node => node.addEventListener('click', () => {
    nav('topic', Number(node.dataset.topic));
  }));
}

function bindTopicDetail(topic) {
  const form = $('#adjust-form');
  $('#adjust-toggle')?.addEventListener('click', () => { form.hidden = !form.hidden; });
  $$('#adjust-form [data-delta]').forEach(button => button.addEventListener('click', async () => {
    const note = $('#adjust-note').value.trim();
    if (!note) {
      toast(T().wrong.adjustWhy);
      $('#adjust-note').focus();
      return;
    }
    button.disabled = true;
    try {
      await api(`/v1/h5/topic/${topic.topic_id}/adjust${qs()}`, {
        method: 'POST', body: { delta: Number(button.dataset.delta), note },
      });
      toast(T().wrong.adjustSaved);
      await nav('topic', topic.topic_id);
    } catch (err) {
      button.disabled = false;
      if (signedOut(err)) return;
      toast(T().common.error);
    }
  }));
}

function bindList() {
  $$('#view [data-source]').forEach(button => button.addEventListener('click', async () => {
    listSource = button.dataset.source;
    await nav('list');
  }));
  // "看更多" appends into the card container rather than re-rendering: a parent reading down the list
  // should not be thrown back to the top by the next page arriving.
  $('#more')?.addEventListener('click', async () => {
    try {
      const more = await loadWrong(30, LIST_PAGE, listOffset, listSource);
      listOffset += more.items.length;
      $('#cards')?.insertAdjacentHTML('beforeend', more.items.map(qcard).join(''));
      if (listOffset >= more.total) $('#more')?.remove();
    } catch (err) {
      if (signedOut(err)) return;
      toast(T().common.error);
    }
  });
}

function bindProgress() {
  const input = $('#start-date');
  input?.addEventListener('change', async () => {
    if (!input.value) return;
    try {
      const data = await api(`/v1/h5/curriculum${qs()}`, {
        method: 'PUT', body: { start_date: input.value },
      });
      toast(T().settings.progressSaved);
      $('#view').innerHTML = renderProgress(data);
      bindProgress();
      $('#back')?.addEventListener('click', () => go(view));
    } catch (err) {
      if (signedOut(err)) return;
      toast(err.code === 'bad-date' ? T().settings.badDate : T().common.error);
    }
  });
  $$('#view [data-book]').forEach(select => select.addEventListener('change', async () => {
    try {
      const data = await api(`/v1/h5/curriculum/unit${qs()}`, {
        method: 'PUT',
        body: { textbook_id: Number(select.dataset.book), ordinal: Number(select.value) },
      });
      toast(T().settings.progressSaved);
      $('#view').innerHTML = renderProgress(data);
      bindProgress();
      $('#back')?.addEventListener('click', () => go(view));
    } catch (err) {
      if (signedOut(err)) return;
      toast(T().common.error);
    }
  }));
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
  if (name === 'wrong') {
    // Two calls in parallel: the overview shows both halves, and waiting for them in sequence would
    // leave the card empty for as long as the second one takes.
    return Promise.all([loadWeak(), loadWrong(30, 2, 0, 'all')])
      .then(([mastery, wrong]) => ({ mastery, wrong }));
  }
  if (name === 'settings') return api(`/v1/h5/config?${tv}`);
  return null;
}

async function go(name) {
  view = name;
  page = null;
  pageArg = null;
  $$('.tab').forEach(tab => tab.classList.toggle('is-on', tab.dataset.tab === name));
  const host = $('#view');
  host.classList.remove('has-dock');
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
      host.classList.add('has-dock');
      host.innerHTML = renderWeakOverview(data.mastery, data.wrong);
      $$('#view [data-go]').forEach(link => link.addEventListener('click', event => {
        event.preventDefault();
        nav(link.dataset.go);
      }));
      $$('#view [data-topic]').forEach(row => row.addEventListener('click', () => {
        nav('topic', Number(row.dataset.topic));
      }));
      $('#add-open')?.addEventListener('click', openAdd);
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
  if (view === 'settings' && !page) return;
  // Re-open the same sub-page after a language switch or a recorded mistake, rather than dropping the
  // parent back on the tab root.
  if (page) nav(page, pageArg);
  else go(view);
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

  $('#progress')?.addEventListener('click', () => nav('progress'));
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
    // Tapping the tab you are already on is a no-op — except from a sub-page, where it is the shortest
    // way back to the root and does not need the back arrow.
    if (tab.dataset.tab === view && page) { go(view); return; }
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
  ['#auth-password', '#auth-password2', '#auth-email', '#auth-question-input', '#auth-answer',
    '#auth-old-answer'].forEach(id => {
    $(id).addEventListener('input', () => {
      paintRules();
      $('#auth-error').hidden = true;
    });
  });
  $('#device-switch').addEventListener('click', openSheet);
  $('#sheet-close').addEventListener('click', closeSheet);
  $('#add-close').addEventListener('click', closeAdd);
  $('#add-backdrop').addEventListener('click', event => {
    if (event.target === $('#add-backdrop')) closeAdd();
  });
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
