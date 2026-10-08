@echo off
chcp 65001 >nul
title tg-cli (Demo / 10k Sample Chats)
py -3.12 -m tg_cli --fake %*

