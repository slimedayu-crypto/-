[app]
title = MusicBox Pro
package.name = musicboxpro
package.domain = org.musicboxpro
source.dir = .
source.include_exts = py,png,jpg,kv,atlas,json
version = 1.0

requirements = python3,kivy==2.3.0,yt-dlp,mutagen,requests,certifi,plyer,android,pyjnius,urllib3,charset-normalizer,idna

orientation = portrait
fullscreen = 0

android.permissions = INTERNET,READ_EXTERNAL_STORAGE,WRITE_EXTERNAL_STORAGE
android.api = 33
android.minapi = 24
android.ndk = 23b
android.archs = arm64-v8a, armeabi-v7a
android.allow_backup = True

[buildozer]
log_level = 2
warn_on_root = 1
