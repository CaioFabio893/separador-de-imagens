#!/usr/bin/env bash
# Abre o Separador de Imagens no navegador.
# Sobe o servidor local (necessário para o navegador salvar as imagens e as
# marcações entre sessões e para a ponte opcional com o Ollama) e abre a página.

set -u

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_PORT="8765"
PORT_FILE="/tmp/separador_imagens_port"
LOG_FILE="/tmp/separador_imagens.log"

cd "$DIR" || exit 1

health() {
  curl -s --max-time 2 "http://127.0.0.1:$1/api/ollama/status" 2>/dev/null \
    | grep -q '"servidor"[[:space:]]*:[[:space:]]*"separador"'
}

URL=""

# Reaproveita um servidor já iniciado por este atalho.
if [ -f "$PORT_FILE" ]; then
  SAVED_PORT="$(cat "$PORT_FILE" 2>/dev/null || true)"
  case "$SAVED_PORT" in
    ''|*[!0-9]*) ;;
    *)
      if health "$SAVED_PORT"; then
        URL="http://127.0.0.1:${SAVED_PORT}/numerador_matriz.html"
      fi
      ;;
  esac
fi

# Se o servidor estiver rodando mas o arquivo de porta sumiu, procura perto.
if [ -z "$URL" ]; then
  for PORT in $(seq "$BASE_PORT" $((BASE_PORT + 10))); do
    if health "$PORT"; then
      URL="http://127.0.0.1:${PORT}/numerador_matriz.html"
      break
    fi
  done
fi

# Inicia o servidor (ele escolhe a próxima porta livre se 8765 estiver ocupada).
if [ -z "$URL" ]; then
  rm -f "$PORT_FILE"
  nohup python3 "$DIR/servidor_separador.py" \
    --port "$BASE_PORT" --port-file "$PORT_FILE" \
    >>"$LOG_FILE" 2>&1 &

  for _ in $(seq 1 50); do
    [ -s "$PORT_FILE" ] && break
    sleep 0.2
  done

  CHOSEN_PORT="$(cat "$PORT_FILE" 2>/dev/null || true)"
  case "$CHOSEN_PORT" in
    ''|*[!0-9]*) CHOSEN_PORT="" ;;
  esac
  if [ -n "$CHOSEN_PORT" ] && health "$CHOSEN_PORT"; then
    URL="http://127.0.0.1:${CHOSEN_PORT}/numerador_matriz.html"
  fi
fi

if [ -z "$URL" ]; then
  echo "Não foi possível iniciar o servidor local. Veja o log em: $LOG_FILE" >&2
  exit 1
fi

if command -v xdg-open >/dev/null 2>&1; then
  xdg-open "$URL" >/dev/null 2>&1 &
else
  echo "Abra no navegador: $URL"
fi

exit 0
