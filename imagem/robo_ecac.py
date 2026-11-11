"""
Robô e-CAC: abre o portal, clica em "Acesso Gov BR" e PARA na imagem de verificação.

O print da imagem é salvo em PASTA_PRINTS (sempre como imagem.png) e enviado
sozinho ao DeepSeek pelo aplicativo local (PDF numerado). A resposta em números
aparece no terminal e o robô clica nas imagens.

Como rodar:
    python robo_ecac.py            (ou abra o ROBO.bat)  -> automático
    python robo_ecac.py --manual   -> responde pelo teclado
"""
import glob
import os
import sys
import time

from selenium import webdriver
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

import resposta_deepseek

URL_ECAC = "https://cav.receita.fazenda.gov.br/autenticacao/login"
PASTA_PRINTS = r"C:\Users\usuario\Desktop\separadro de imagens\imagem"
NOME_PRINT = "imagem.png"

# Posição de cada imagem e do botão, em fração do tamanho do quadro
# (medido no print: quadro 500x750, grade 3x3, botão no canto inferior direito)
POS_IMAGENS = {
    1: (0.176, 0.317), 2: (0.500, 0.317), 3: (0.824, 0.317),
    4: (0.176, 0.533), 5: (0.500, 0.533), 6: (0.824, 0.533),
    7: (0.176, 0.749), 8: (0.500, 0.749), 9: (0.824, 0.749),
}
POS_BOTAO = (0.872, 0.953)


def abrir_navegador():
    opcoes = webdriver.ChromeOptions()
    opcoes.add_argument("--start-maximized")
    opcoes.add_experimental_option("excludeSwitches", ["enable-automation"])
    opcoes.add_experimental_option("useAutomationExtension", False)
    opcoes.add_argument("--disable-blink-features=AutomationControlled")
    # Mantém o Chrome aberto quando o script terminar
    opcoes.add_experimental_option("detach", True)
    return webdriver.Chrome(options=opcoes)


def achar_quadro(driver):
    """Acha o quadro do desafio: o MAIOR iframe do hCaptcha visível na tela.
    (Não usa o título do iframe, porque ele muda conforme o idioma do Chrome.)"""
    driver.switch_to.default_content()
    melhor, maior_area = None, 0
    for elemento in driver.find_elements(By.CSS_SELECTOR, "iframe[src*='hcaptcha']"):
        try:
            if not elemento.is_displayed():
                continue
            tamanho = elemento.size
            area = tamanho["width"] * tamanho["height"]
            if tamanho["width"] > 200 and tamanho["height"] > 200 and area > maior_area:
                melhor, maior_area = elemento, area
        except Exception:
            continue
    return melhor


def esperar_quadro(driver, segundos=120):
    inicio = time.time()
    while time.time() - inicio < segundos:
        quadro = achar_quadro(driver)
        if quadro is not None:
            return quadro
        time.sleep(1)
    return None


def tirar_print(driver, segundos=25, diferente_de=None):
    """Salva o print só depois que a imagem de verificação termina de carregar.

    Antes o print era tirado com 3 segundos fixos e às vezes pegava a grade
    pela metade (parte das imagens ainda em branco). Agora espera o quadro
    ficar igual em capturas seguidas, o que indica que o carregamento acabou.

    Com diferente_de, espera a imagem trocar (desafio novo depois do Verificar)
    e devolve None se o mesmo desafio continuar na tela.
    """
    inicio = time.time()
    anterior, estavel, dados = None, 0, None
    while time.time() - inicio < segundos:
        quadro = achar_quadro(driver)
        if quadro is None:
            print("Não achei a imagem na tela para tirar o print.")
            return None
        try:
            dados = quadro.screenshot_as_png
        except Exception:
            time.sleep(1)
            continue
        if diferente_de is not None and dados == diferente_de:
            # Ainda é o desafio antigo: espera o site trocar a imagem.
            anterior, estavel = None, 0
            time.sleep(1)
            continue
        if dados == anterior:
            estavel += 1
            # Estável por 3 segundos seguidos: a grade terminou de aparecer.
            if estavel >= 2 and time.time() - inicio >= 3:
                break
        else:
            estavel = 0
        anterior = dados
        time.sleep(1)

    if not dados:
        print("Não consegui capturar a imagem.")
        return None
    if diferente_de is not None and dados == diferente_de:
        print("A imagem não trocou depois do Verificar (mesmo desafio na tela).")
        return None

    os.makedirs(PASTA_PRINTS, exist_ok=True)
    # Apaga prints antigos com o nome velho (captcha_*.png)
    for antigo in glob.glob(os.path.join(PASTA_PRINTS, "captcha_*.png")):
        try:
            os.remove(antigo)
        except OSError:
            pass

    caminho = os.path.join(PASTA_PRINTS, NOME_PRINT)
    with open(caminho, "wb") as arquivo:
        arquivo.write(dados)  # sobrescreve o imagem.png antigo
    print(f"Print da imagem salvo em: {caminho}")
    return caminho


def clicar_no_quadro(driver, quadro, fx, fy):
    """Clica num ponto do quadro (fx, fy = fração da largura/altura)."""
    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", quadro)
    time.sleep(0.2)
    largura, altura = quadro.size["width"], quadro.size["height"]
    # No Selenium 4 o deslocamento é a partir do CENTRO do elemento
    dx = int(largura * fx - largura / 2)
    dy = int(altura * fy - altura / 2)
    ActionChains(driver).move_to_element_with_offset(quadro, dx, dy).click().perform()


def clicar_numeros(driver, numeros):
    """Clica nas imagens indicadas, confirma e tira o print da próxima."""
    quadro = achar_quadro(driver)
    if quadro is None:
        print("A imagem não está na tela (talvez já foi resolvida).")
        return None

    clicou = False
    for n in numeros:
        if n not in POS_IMAGENS:
            print(f"'{n}' não é válido. Use 1 a 9.")
            continue
        clicar_no_quadro(driver, quadro, *POS_IMAGENS[n])
        time.sleep(0.3)
        print(f"Cliquei na imagem {n}.")
        clicou = True

    if not clicou:
        return None

    # Tira o mouse de cima das imagens antes de comparar os prints, para o
    # realce do mouse não parecer uma imagem nova.
    time.sleep(0.5)
    try:
        ActionChains(driver).move_to_element_with_offset(quadro, 4, 4).perform()
        time.sleep(0.3)
    except Exception:
        pass
    try:
        antes = quadro.screenshot_as_png
    except Exception:
        antes = None

    clicar_no_quadro(driver, quadro, *POS_BOTAO)
    print("Cliquei em Verificar. Aguardando a próxima imagem...")
    time.sleep(1.0)
    try:
        ActionChains(driver).move_to_element_with_offset(quadro, 4, 4).perform()
    except Exception:
        pass
    return tirar_print(driver, diferente_de=antes)


def processar_resposta(driver):
    """Lê uma resposta digitada e executa. Devolve False quando é para sair."""
    resposta = input("\nSua resposta: ").strip().lower()
    if not resposta:
        return True
    if resposta == "s":
        return False
    if resposta == "p":
        tirar_print(driver)
        return True

    quadro = achar_quadro(driver)
    if quadro is None:
        print("A imagem não está na tela (talvez já foi resolvida). Digite p ou s.")
        return True

    if resposta == "v":
        clicar_no_quadro(driver, quadro, *POS_BOTAO)
        print("Cliquei no botão. Tirando print da próxima imagem...")
        tirar_print(driver)
        return True

    numeros = [int(n) for n in resposta.replace(",", " ").split() if n.isdigit()]
    if not numeros:
        print("Use 1 a 9, v, p ou s.")
        return True
    clicar_numeros(driver, numeros)
    return True


def modo_manual(driver):
    while True:
        try:
            if not processar_resposta(driver):
                return
        except Exception as erro:
            texto = str(erro).splitlines()[0] if str(erro) else ""
            print(f"Deu erro: {type(erro).__name__}: {texto}")
            print("Tente de novo, ou digite p para tirar um print novo.")


def modo_automatico(driver):
    """Pega a imagem, recebe os números do DeepSeek e clica sozinho."""
    while True:
        caminho = os.path.join(PASTA_PRINTS, NOME_PRINT)
        numeros = []
        for _ in range(3):
            try:
                print("\nEnviando a imagem ao DeepSeek...")
                numeros = resposta_deepseek.resolver(caminho)
            except resposta_deepseek.LeituraIncompleta as erro:
                print(f"A imagem não carregou inteira ({erro}). Tirando outro print...")
                time.sleep(2)
                if tirar_print(driver) is None:
                    return
                continue
            except Exception as erro:
                print(f"Resposta automática não deu certo: {erro}")
                break
            if numeros:
                break
            print("O DeepSeek não indicou nenhuma imagem. Tirando outro print...")
            time.sleep(2)
            if tirar_print(driver) is None:
                return

        if not numeros:
            print("Responda manualmente (1 a 9, v, p ou s).")
            if not processar_resposta(driver):
                return
            continue

        print("Resposta do DeepSeek: " + " ".join(str(n) for n in numeros))
        if clicar_numeros(driver, numeros) is None:
            return


def main():
    manual = "--manual" in sys.argv[1:]
    driver = abrir_navegador()
    print("Abrindo o e-CAC...")
    driver.get(URL_ECAC)

    # Clica no botão "Acesso Gov BR"
    try:
        botao = WebDriverWait(driver, 30).until(
            EC.element_to_be_clickable(
                (By.XPATH, "//input[@type='image' and (contains(@src,'gov-br') or @alt='Acesso Gov BR')]")
            )
        )
        botao.click()
        print("Cliquei em 'Acesso Gov BR'.")
    except Exception:
        print("Botão 'Acesso Gov BR' não encontrado; seguindo na página atual.")

    print("Aguardando a imagem aparecer...")
    if esperar_quadro(driver) is None:
        print("A imagem não apareceu em 2 minutos. Digite p para tentar o print de novo.")
    else:
        tirar_print(driver)

    print("\n" + "=" * 60)
    if manual:
        print(" MODO MANUAL. Responda pelo teclado:")
        print("   1 a 9      -> clica nas imagens e depois em Verificar (ex.: 1 5 9)")
        print("              1 2 3")
        print("              4 5 6   <- posição das imagens")
        print("              7 8 9")
        print("   v          -> clica no botão Verificar / Pular / Próximo")
        print("   p          -> tira um print novo da imagem")
        print("   s          -> sair (o navegador continua aberto)")
    else:
        print(" MODO AUTOMÁTICO. A resposta vem do DeepSeek e aparece aqui.")
        print("   Se o automático falhar, responda pelo teclado: 1 a 9, v, p ou s.")
    print("=" * 60)

    if manual:
        modo_manual(driver)
    else:
        modo_automatico(driver)
    print("Robô encerrado. O navegador continua aberto.")


if __name__ == "__main__":
    main()
