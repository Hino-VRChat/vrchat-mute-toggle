# VRChat Mute Toggle

VRChatのミュート状態をOSC（Open Sound Control）経由でトグルするデスクトップツールです。
グローバルホットキーで操作でき、タスクトレイに常駐します。

## 機能

- カスタマイズ可能なグローバルホットキーでミュート切り替え
- VRChatのミュート状態とリアルタイム同期（OSC受信）
- タスクトレイ常駐（最小化しても動作）
- VRChatプロセスの自動検出

## 動作要件

- Windows 10/11
- VRChat（OSC機能を有効化）
- VRChatのマイクの動作を「切り替え」に設定

## 使い方
1. VRChatを起動し、OSCを有効化（Action Menu → Options → OSC → Enabled）
2. `VRChatMuteToggle.exe` を実行（Python環境があれば`python vrchat_mute_toggle.py`でも可）
3. Right Ctrl（デフォルト）でミュートを切り替え

ホットキーはGUI上の「変更」ボタンから変更できます。

### Pythonで使う場合
```
pip install python-osc keyboard pystray Pillow
python vrchat_mute_toggle.py
```

## ビルド

```
python -m nuitka --standalone --onefile --windows-console-mode=disable --output-dir=./dist --output-filename=VRChatMuteToggle.exe --enable-plugin=tk-inter ./vrchat_mute_toggle.py
```

## ライセンス

MIT License