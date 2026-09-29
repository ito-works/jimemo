---
title: "週次ブリーフィング — Weekly briefing (synthetic fixture)"
date: "2026-09-30"
kicker: "Phone reader fixture"
subtitle: "日本語と英語の本文、横長の表、インライン SVG を含む合成データ。"
---
This page is a synthetic fixture for checking jimemo output at phone
width. None of it describes real people, projects or figures.

## 概要

今週は三つの作業が予定どおりに進み、一つが一週間遅れた。遅れの原因は外部の
承認待ちで、来週の月曜日に解消する見込みである。長い段落が狭い画面で正しく
折り返されるかを確認するため、この段落は意図的に長くしてある。句読点、全角の
括弧（例：このように）、数字 1,234 と英単語 mixed-script text が同じ行に並んだ
ときの改行位置も確認対象である。

## Status table

A table wider than a phone screen. It should scroll inside its own box
while the page itself stays at the screen width.

| Workstream | Owner | Start | Due | Status | Budget (¥k) | Spent (¥k) | Remaining (¥k) | Notes |
|---|---|---|---|---|---:|---:|---:|---|
| 設計レビュー | Team A | 2026-09-01 | 2026-09-15 | 完了 | 1,200 | 1,150 | 50 | on time |
| Data import | Team B | 2026-09-08 | 2026-09-29 | 遅延 | 2,400 | 2,610 | -210 | waiting on approval |
| 翻訳 | Team C | 2026-09-10 | 2026-10-10 | 進行中 | 800 | 310 | 490 | glossary agreed |

## Flow

The diagram below is an inline SVG spliced from a local file; it should
scale down to the screen width and keep its text legible.

![Process flow: draft, review, publish](flow.svg)

## まとめ

来週は Data import の承認を得て、翻訳の用語集を最終化する。
