#!/usr/bin/env bash
# Что уже стоит на сервере. Запуск: curl -fsSL <raw-url> | bash   или   bash deploy/shared/diagnose.sh
echo "== ОС ==";        . /etc/os-release 2>/dev/null; echo "$PRETTY_NAME"
echo "== CPU / RAM / диск =="; nproc; free -h | sed -n 1,2p; df -h / | tail -1
echo "== Кто слушает 80/443/8080 =="; (ss -ltnp 2>/dev/null || netstat -ltnp) | grep -E ':(80|443|8080|5432)\b' || echo "свободно"
echo "== Веб-серверы =="; for s in nginx caddy apache2 httpd; do systemctl is-active --quiet $s 2>/dev/null && echo "$s: active (systemd)"; done
command -v docker >/dev/null && { echo "== Docker =="; docker --version; docker compose version 2>/dev/null; docker ps --format '{{.Names}}\t{{.Image}}\t{{.Ports}}'; } || echo "Docker: нет"
echo "== nginx server_name =="; grep -rhoE 'server_name[^;]+' /etc/nginx/ 2>/dev/null | sort -u
echo "== Caddyfile сайты =="; grep -hE '^[a-z0-9.*-]+\.[a-z]+' /etc/caddy/Caddyfile 2>/dev/null
echo "== certbot =="; command -v certbot >/dev/null && certbot certificates 2>/dev/null | grep -E 'Domains|Expiry' || echo "certbot: нет"
