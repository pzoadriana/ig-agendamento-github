"""
Publicador do Instagram via GitHub Actions.
Roda todo dia as 11h (BRT) pelo workflow publicar.yml.
Le a fila.json, publica o conteudo do dia e atualiza o status.
"""
import os
import json
import time
import urllib.request
import urllib.parse
import urllib.error
from datetime import date, datetime, timezone, timedelta

FILA_FILE  = os.path.join(os.path.dirname(__file__), 'fila', 'fila.json')
VIDEO_DIR  = os.path.join(os.path.dirname(__file__), 'videos')

IG_TOKEN   = os.environ['IG_ACCESS_TOKEN']
IG_ID      = os.environ['IG_BUSINESS_ID']
API_BASE   = 'https://graph.instagram.com/v21.0'
GITHUB_REPO = os.environ.get('GITHUB_REPOSITORY', '')
GITHUB_TOKEN = os.environ.get('GITHUB_TOKEN', '')
RAW_BASE   = f'https://raw.githubusercontent.com/{GITHUB_REPO}/main/videos'


def _post(url, params, timeout=30):
    data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(url, data=data, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raise ValueError(f'HTTP {e.code}: {e.read().decode()}')


def _get(url, timeout=15):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode())


def aguardar_processamento(container_id):
    for i in range(30):
        time.sleep(10)
        url = f'{API_BASE}/{container_id}?fields=status_code&access_token={IG_TOKEN}'
        status = _get(url)
        code = status.get('status_code', '')
        print(f'  [{i+1}/30] Status: {code}')
        if code == 'FINISHED':
            return
        if code == 'ERROR':
            raise ValueError(f'Erro no processamento: {status}')
    raise TimeoutError('Instagram demorou demais para processar.')


def publicar_reel(item):
    video_file = item['video_file']
    legenda    = item.get('legenda', '')
    video_url  = f'{RAW_BASE}/{video_file}'

    print(f'Criando container para {video_file}...')
    result = _post(f'{API_BASE}/{IG_ID}/media', {
        'media_type':    'REELS',
        'video_url':     video_url,
        'caption':       legenda,
        'share_to_feed': 'true',
        'access_token':  IG_TOKEN,
    })
    container_id = result.get('id')
    if not container_id:
        raise ValueError(f'Erro ao criar container: {result}')

    print(f'Container criado: {container_id}. Aguardando processamento...')
    aguardar_processamento(container_id)

    print('Publicando...')
    pub = _post(f'{API_BASE}/{IG_ID}/media_publish', {
        'creation_id':  container_id,
        'access_token': IG_TOKEN,
    })
    post_id = pub.get('id')
    if not post_id:
        raise ValueError(f'Erro ao publicar: {pub}')

    print(f'Publicado! Post ID: {post_id}')
    return post_id


def publicar_carrossel(item):
    midias   = item.get('midias', [])
    legenda  = item.get('legenda', '')

    child_ids = []
    for m in midias:
        url = f'{RAW_BASE}/{m["arquivo"]}'
        tipo = m.get('tipo', 'IMAGE')
        params = {'access_token': IG_TOKEN}
        if tipo == 'VIDEO':
            params.update({'media_type': 'VIDEO', 'video_url': url, 'is_carousel_item': 'true'})
        else:
            params.update({'image_url': url, 'is_carousel_item': 'true'})
        result = _post(f'{API_BASE}/{IG_ID}/media', params)
        cid = result.get('id')
        if not cid:
            raise ValueError(f'Erro ao criar item do carrossel: {result}')
        if tipo == 'VIDEO':
            aguardar_processamento(cid)
        child_ids.append(cid)

    result = _post(f'{API_BASE}/{IG_ID}/media', {
        'media_type':   'CAROUSEL',
        'children':     ','.join(child_ids),
        'caption':      legenda,
        'access_token': IG_TOKEN,
    })
    container_id = result.get('id')
    if not container_id:
        raise ValueError(f'Erro ao criar container carrossel: {result}')

    pub = _post(f'{API_BASE}/{IG_ID}/media_publish', {
        'creation_id':  container_id,
        'access_token': IG_TOKEN,
    })
    post_id = pub.get('id')
    if not post_id:
        raise ValueError(f'Erro ao publicar carrossel: {pub}')

    print(f'Carrossel publicado! Post ID: {post_id}')
    return post_id


def publicar_imagem(item):
    arquivo = item['arquivo']
    legenda = item.get('legenda', '')
    url     = f'{RAW_BASE}/{arquivo}'

    result = _post(f'{API_BASE}/{IG_ID}/media', {
        'image_url':    url,
        'caption':      legenda,
        'access_token': IG_TOKEN,
    })
    container_id = result.get('id')
    if not container_id:
        raise ValueError(f'Erro ao criar container imagem: {result}')

    pub = _post(f'{API_BASE}/{IG_ID}/media_publish', {
        'creation_id':  container_id,
        'access_token': IG_TOKEN,
    })
    post_id = pub.get('id')
    print(f'Imagem publicada! Post ID: {post_id}')
    return post_id


def main():
    hoje = date.today().isoformat()
    print(f'=== Publicador Instagram — {hoje} ===')

    with open(FILA_FILE, encoding='utf-8') as f:
        fila = json.load(f)

    conteudos = fila.get('conteudos', [])
    pendentes = [c for c in conteudos if c.get('data') == hoje and c.get('status') != 'publicado']

    if not pendentes:
        print(f'Nenhum conteudo para publicar hoje ({hoje}).')
        return

    arquivos_para_apagar = []
    erros = 0
    for item in pendentes:
        tipo = item.get('tipo', 'reel')
        titulo = item.get('titulo', '')[:50]
        print(f'\nPublicando [{tipo}]: {titulo}')
        try:
            if tipo == 'reel':
                post_id = publicar_reel(item)
            elif tipo == 'carrossel':
                post_id = publicar_carrossel(item)
            elif tipo == 'imagem':
                post_id = publicar_imagem(item)
            else:
                print(f'Tipo desconhecido: {tipo}. Pulando.')
                continue

            item['status']       = 'publicado'
            item['publicado_em'] = datetime.now(timezone(timedelta(hours=-3))).isoformat()
            item['post_id']      = post_id

            # Marca arquivos de midia para apagar apos publicar
            if tipo == 'reel' and item.get('video_file'):
                arquivos_para_apagar.append(os.path.join(VIDEO_DIR, item['video_file']))
            elif tipo == 'imagem' and item.get('arquivo'):
                arquivos_para_apagar.append(os.path.join(VIDEO_DIR, item['arquivo']))
            elif tipo == 'carrossel':
                for m in item.get('midias', []):
                    if m.get('arquivo'):
                        arquivos_para_apagar.append(os.path.join(VIDEO_DIR, m['arquivo']))

        except Exception as e:
            print(f'ERRO: {e}')
            item['status'] = 'erro'
            item['erro']   = str(e)
            erros += 1

    # Apaga os arquivos de midia publicados para liberar espaco no repositorio
    for caminho in arquivos_para_apagar:
        try:
            if os.path.exists(caminho):
                os.remove(caminho)
                print(f'Arquivo removido: {os.path.basename(caminho)}')
        except Exception as e:
            print(f'Aviso: nao consegui apagar {caminho}: {e}')

    with open(FILA_FILE, 'w', encoding='utf-8') as f:
        json.dump(fila, f, ensure_ascii=False, indent=2)

    print(f'\n=== Concluido. {len(pendentes) - erros} publicado(s), {erros} erro(s). ===')
    if erros:
        exit(1)


if __name__ == '__main__':
    main()
