# v3 hotkey fix

- Replaced `RegisterHotKey` / low-level hook handling with Windows `GetAsyncKeyState` polling.
- This is more reliable while DirectX games such as League of Legends have focus.
- F8 is edge-detected, so holding it creates only one clip.
- The log now prints `Global F8 press detected` whenever the key is seen.

## If League is running as administrator

Windows can restrict input visibility across privilege levels. Start PyCharm or the packaged app as administrator too.
