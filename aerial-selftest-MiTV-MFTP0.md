### Aerial self-test

MiTV-MFTP0, Android 9, Aerial 1.69, 2026-11-01

```
guard              PASS Home Guard bound; default home is com.mitv.tvhome/.MainActivityUserMode
home_on_aerial     FAIL Aerial in front, then com.mitv.tvhome 4 s later
home_from_app      PASS from com.duokan.videodaily, Aerial in front after 5.9 s
standby            SKIP STANDBY=1 to try
reboot             SKIP REBOOT=1 to try (ask the owner first)
inputs             SKIP 10 listed; INPUTS=1 to open each
parental_clock     PASS two minutes counted, the two-minute day used up
parental_lock      PASS the lock came up in app.orchard.tv/app.orchard.tv.LockActivity
parental_keys      PASS Back and Menu left the lock in front; Home: the launcher showed, the lock came back (injected Home skips the guard's key filter — the remote is the real test)
parental_guard     PASS the lock stayed in front when another app was opened
parental_exit      PASS 加 30 分钟 lifted the lock and Home is back in front (keypad not typed)
parental_break     PASS one minute of the session used: the countdown screen, 660 s on it
parental_pin       PASS the password went through and 30 minutes were added
  (parental: put back — Normal, 120000 ms used today, limits {"daily":2,"session":0,"interval":10})
```

**Inputs as Aerial lists them**
```json
[{"id":"com.mediatek.tvinput\/.hdmi.HDMIInputService\/HW5","label":"HDMI 1","kind":"Hdmi","shown":true,"hw":5,"firmwareLabel":"HDMI 1","xiaomiInput":23},{"id":"com.mediatek.tvinput\/.hdmi.HDMIInputService\/HW6","label":"HDMI 2","kind":"Hdmi","shown":true,"hw":6,"firmwareLabel":"HDMI 2","xiaomiInput":24},{"id":"com.mediatek.tvinput\/.hdmi.HDMIInputService\/HW7","label":"HDMI 3","kind":"Hdmi","shown":true,"hw":7,"firmwareLabel":"HDMI 3","xiaomiInput":25},{"id":"com.mediatek.tvinput\/.hdmi.HDMIInputService\/HW8","label":"HDMI 4","kind":"Hdmi","shown":true,"hw":8,"firmwareLabel":"HDMI 4","xiaomiInput":26},{"id":"com.mediatek.tvinput\/.tuner.TunerInputService\/HW1","label":"Tuner(ATV)","kind":"Tuner","shown":true,"hw":1,"firmwareLabel":"Tuner(ATV)"},{"id":"com.mediatek.tvinput\/.tuner.TunerInputService\/HW0","label":"Tuner(DTV)","kind":"Tuner","shown":true,"hw":0,"firmwareLabel":"Tuner(DTV)"},{"id":"com.mediatek.tvinput\/.component.ComponentInputService\/HW3","label":"Component","kind":"Av","shown":true,"hw":3,"firmwareLabel":"Component","xiaomiInput":2},{"id":"com.mediatek.tvinput\/.composite.CompositeInputService\/HW2","label":"Composite","kind":"Av","shown":true,"hw":2,"firmwareLabel":"Composite","xiaomiInput":2},{"id":"com.mediatek.tvinput\/.vga.VGAInputService\/HW4","label":"VGA","kind":"Other","shown":true,"hw":4,"firmwareLabel":"VGA"},{"id":"usb","label":"USB","kind":"Usb","shown":true}]
```

**Self-test result**
```json
{"model":"MiTV-MFTP0","aerial":"1.69","checks":{"guard":{"result":"pass","detail":"Home Guard bound; default home is com.mitv.tvhome/.MainActivityUserMode"},"home_on_aerial":{"result":"fail","detail":"Aerial in front, then com.mitv.tvhome 4 s later"},"home_from_app":{"result":"pass","detail":"from com.duokan.videodaily, Aerial in front after 5.9 s"},"standby":{"result":"skip","detail":"STANDBY=1 to try"},"reboot":{"result":"skip","detail":"REBOOT=1 to try (ask the owner first)"},"inputs":{"result":"skip","detail":"10 listed; INPUTS=1 to open each"},"parental_clock":{"result":"pass","detail":"two minutes counted, the two-minute day used up"},"parental_lock":{"result":"pass","detail":"the lock came up in app.orchard.tv/app.orchard.tv.LockActivity"},"parental_keys":{"result":"pass","detail":"Back and Menu left the lock in front; Home: the launcher showed, the lock came back (injected Home skips the guard's key filter — the remote is the real test)"},"parental_guard":{"result":"pass","detail":"the lock stayed in front when another app was opened"},"parental_exit":{"result":"pass","detail":"加 30 分钟 lifted the lock and Home is back in front (keypad not typed)"},"parental_break":{"result":"pass","detail":"one minute of the session used: the countdown screen, 660 s on it"},"parental_pin":{"result":"pass","detail":"the password went through and 30 minutes were added"}}}
```
