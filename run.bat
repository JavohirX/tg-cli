@echo off
chcp 65001 >nul
title tg-cli
py -3.12 -m tg_cli %*

