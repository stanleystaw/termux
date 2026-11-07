#!/data/data/com.termux/files/usr/bin/bash
set -e

RAW_REPO="https://raw.githubusercontent.com/stanleystaw/termux/main"
APP_DIR="$HOME/.reels2026"
BIN_DIR="${PREFIX:-/data/data/com.termux/files/usr}/bin"
SHORTCUTS_DIR="$HOME/.shortcuts"
TASKS_DIR="$HOME/.shortcuts/tasks"
BOOT_DIR="$HOME/.termux/boot"

echo "=========================================================="
echo " 📱 INSTALLATION PLAYOUT REELS 2026 — ANDROID TERMUX"
echo " Zéro Shadowban • Playout 6h / (Moyenne + 20 vues)"
echo "=========================================================="

echo "[1/4] Vérification des outils Termux (Python + FFmpeg)..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -y >/dev/null 2>&1 || true
apt-get install -y \
  -o Dpkg::Options::="--force-confdef" \
  -o Dpkg::Options::="--force-confold" \
  python ffmpeg curl cronie termux-api >/dev/null 2>&1 || pkg install -y python ffmpeg curl >/dev/null 2>&1

mkdir -p "$APP_DIR/sessions" "$SHORTCUTS_DIR" "$TASKS_DIR" "$BOOT_DIR" "$BIN_DIR"

echo "[2/4] Téléchargement du moteur Playout natif..."
curl -fsSL "$RAW_REPO/moteur_termux.py?t=$(date +%s)" -o "$APP_DIR/moteur_termux.py"
chmod +x "$APP_DIR/moteur_termux.py"

echo "[3/4] Création des raccourcis 1-clic & commandes rapides..."

# Watchdog (relance le Playout 24/7 si Android l'a mis en veille)
cat > "$APP_DIR/watchdog.sh" << 'EOF'
#!/data/data/com.termux/files/usr/bin/bash
APP_DIR="$HOME/.reels2026"
PID_FILE="$APP_DIR/daemon.pid"
if [ -f "$PID_FILE" ]; then
  PID=$(cat "$PID_FILE" 2>/dev/null || echo "")
  if [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null; then
    exit 0
  fi
fi
command -v termux-wake-lock >/dev/null 2>&1 && termux-wake-lock >/dev/null 2>&1 || true
nohup python3 "$APP_DIR/moteur_termux.py" --daemon >> "$APP_DIR/daemon_stdout.log" 2>&1 &
EOF
chmod +x "$APP_DIR/watchdog.sh"

# Commande 1-mot : `publier` (publie immédiatement la prochaine vidéo !)
cat > "$BIN_DIR/publier" << 'EOF'
#!/data/data/com.termux/files/usr/bin/bash
ACC="${1:-}"
if [ -n "$ACC" ]; then
  python3 "$HOME/.reels2026/moteur_termux.py" --force-now --account "$ACC"
else
  python3 "$HOME/.reels2026/moteur_termux.py" --force-now
fi
EOF
chmod +x "$BIN_DIR/publier"
cp -f "$BIN_DIR/publier" "$HOME/publier"
chmod +x "$HOME/publier"

# Commande 1-mot : `vues` (vérifie les vues en direct + règle Moyenne+20)
cat > "$BIN_DIR/vues" << 'EOF'
#!/data/data/com.termux/files/usr/bin/bash
python3 "$HOME/.reels2026/moteur_termux.py" --once
EOF
chmod +x "$BIN_DIR/vues"
cp -f "$BIN_DIR/vues" "$HOME/vues"
chmod +x "$HOME/vues"

# Commande 1-mot : `playout` (active/vérifie le Playout 24/7 + affiche le journal)
cat > "$BIN_DIR/playout" << 'EOF'
#!/data/data/com.termux/files/usr/bin/bash
"$HOME/.reels2026/watchdog.sh"
command -v crond >/dev/null 2>&1 && (pgrep -x crond >/dev/null 2>&1 || crond) || true
sleep 1
PID=$(cat "$HOME/.reels2026/daemon.pid" 2>/dev/null || echo "?")
echo "=========================================================="
echo " 🟢 PLAYOUT REELS 24/7 ACTIF EN ARRIÈRE-PLAN (PID: $PID)"
echo " Règle : Toutes les 6h OU immédiat dès (Moyenne + 20 vues)"
echo "=========================================================="
if [ -f "$HOME/.reels2026/playout.log" ]; then
  tail -n 20 "$HOME/.reels2026/playout.log"
fi
EOF
chmod +x "$BIN_DIR/playout"
cp -f "$BIN_DIR/playout" "$HOME/playout"
chmod +x "$HOME/playout"

# Commande : `stop-playout`
cat > "$BIN_DIR/stop-playout" << 'EOF'
#!/data/data/com.termux/files/usr/bin/bash
PID_FILE="$HOME/.reels2026/daemon.pid"
if [ -f "$PID_FILE" ]; then
  PID=$(cat "$PID_FILE" 2>/dev/null || echo "")
  [ -n "$PID" ] && kill "$PID" 2>/dev/null || true
  rm -f "$PID_FILE"
fi
command -v termux-wake-unlock >/dev/null 2>&1 && termux-wake-unlock >/dev/null 2>&1 || true
echo "⏹️ Playout mis en pause."
EOF
chmod +x "$BIN_DIR/stop-playout"

# Raccourcis écran d'accueil Android (Termux:Widget -> ~/.shortcuts/)
cat > "$SHORTCUTS_DIR/🚀_Publier_Maintenant.sh" << 'EOF'
#!/data/data/com.termux/files/usr/bin/bash
echo "🚀 Publication immédiate du prochain Reel depuis votre IP mobile..."
python3 "$HOME/.reels2026/moteur_termux.py" --force-now
echo ""
echo "✅ Terminé ! Fermeture automatique dans 5 secondes..."
sleep 5
EOF
chmod +x "$SHORTCUTS_DIR/🚀_Publier_Maintenant.sh"

cat > "$TASKS_DIR/⚡_Publier_En_Silence.sh" << 'EOF'
#!/data/data/com.termux/files/usr/bin/bash
command -v termux-toast >/dev/null 2>&1 && termux-toast "🎬 Publication Reel lancée..." || true
python3 "$HOME/.reels2026/moteur_termux.py" --force-now
EOF
chmod +x "$TASKS_DIR/⚡_Publier_En_Silence.sh"

cat > "$SHORTCUTS_DIR/📊_Vues_et_Moyenne.sh" << 'EOF'
#!/data/data/com.termux/files/usr/bin/bash
echo "📊 Actualisation des vues en direct & calcul (Moyenne + 20)..."
python3 "$HOME/.reels2026/moteur_termux.py" --once
echo ""
sleep 6
EOF
chmod +x "$SHORTCUTS_DIR/📊_Vues_et_Moyenne.sh"

cat > "$SHORTCUTS_DIR/▶️_Playout_24h_ON.sh" << 'EOF'
#!/data/data/com.termux/files/usr/bin/bash
"$PREFIX/bin/playout"
sleep 4
EOF
chmod +x "$SHORTCUTS_DIR/▶️_Playout_24h_ON.sh"

cat > "$SHORTCUTS_DIR/⏹️_Pause_Playout.sh" << 'EOF'
#!/data/data/com.termux/files/usr/bin/bash
"$PREFIX/bin/stop-playout"
sleep 3
EOF
chmod +x "$SHORTCUTS_DIR/⏹️_Pause_Playout.sh"

# Démarrage auto au reboot Android (Termux:Boot)
cat > "$BOOT_DIR/start-reels2026.sh" << 'EOF'
#!/data/data/com.termux/files/usr/bin/bash
command -v termux-wake-lock >/dev/null 2>&1 && termux-wake-lock || true
command -v crond >/dev/null 2>&1 && crond || true
"$HOME/.reels2026/watchdog.sh"
EOF
chmod +x "$BOOT_DIR/start-reels2026.sh"

echo "[4/4] Activation du Playout automatique 24h/24..."
if command -v crontab >/dev/null 2>&1; then
  (crontab -l 2>/dev/null | grep -v "reels2026/watchdog.sh" || true; echo "*/15 * * * * $HOME/.reels2026/watchdog.sh >/dev/null 2>&1") | crontab - 2>/dev/null || true
fi
command -v crond >/dev/null 2>&1 && (pgrep -x crond >/dev/null 2>&1 || crond) || true

# Relance silencieuse à l'ouverture de Termux
touch "$HOME/.bashrc"
if ! grep -q "REELS2026_AUTOSTART" "$HOME/.bashrc" 2>/dev/null; then
  cat >> "$HOME/.bashrc" << 'EOF'

# REELS2026_AUTOSTART
if [ -x "$HOME/.reels2026/watchdog.sh" ]; then
  "$HOME/.reels2026/watchdog.sh" >/dev/null 2>&1
  echo "🟢 Playout Reels 24/7 actif | Commandes : publier • vues • playout"
fi
EOF
fi

# Stopper une éventuelle ancienne instance puis lancer la nouvelle
if [ -f "$APP_DIR/daemon.pid" ]; then
  OLD_PID=$(cat "$APP_DIR/daemon.pid" 2>/dev/null || echo "")
  [ -n "$OLD_PID" ] && kill "$OLD_PID" 2>/dev/null || true
  rm -f "$APP_DIR/daemon.pid"
fi
"$APP_DIR/watchdog.sh"
sleep 1

echo ""
echo "=========================================================="
echo " ✅ INSTALLATION TERMINÉE — PLAYOUT 24/7 EN LIGNE !"
echo "=========================================================="
echo " 👉 Dans Termux, tapez simplement :"
echo "    publier    -> Publie immédiatement la prochaine vidéo"
echo "    vues       -> Vérifie les vues & le seuil (Moyenne + 20)"
echo "    playout    -> Voir l'état du Playout automatique 24/7"
echo ""
echo " 👉 Raccourci 1-clic sur l'écran d'accueil Android :"
echo "    Ajoutez le widget 'Termux:Widget' sur votre écran :"
echo "    vous y trouverez le bouton '🚀_Publier_Maintenant' !"
echo "=========================================================="
python3 "$APP_DIR/moteur_termux.py" --once || true
