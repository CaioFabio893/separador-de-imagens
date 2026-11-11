"""
Robô e-CAC: abre o portal, clica em "Acesso Gov BR" e PARA na imagem de verificação.
Salva um print só da imagem em PASTA_PRINTS (sempre como imagem.png) e deixa
você responder pelo teclado: digite os números das imagens e o robô clica.

Como rodar:
    python robo_ecac.py      (ou abra o ROBO.bat)
"""
import glob
import os
import time

from selenium import webdriver
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

URL_ECAC = "https://cav.receita.fazenda.gov.br/autenticacao/login"
# O print é salvo na MESMA pasta onde está este arquivo (funciona em qualquer computador)
PASTA_PRINTS = os.path.dirname(os.path.abspath(__file__))
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


def tirar_print(driver):
    """Salva um print só da imagem de verificação, substituindo o anterior."""
    time.sleep(3)  # dá tempo das imagens do desafio carregarem
    quadro = achar_quadro(driver)
    if quadro is None:
        print("Não achei a imagem na tela para tirar o print.")
        return None

    os.makedirs(PASTA_PRINTS, exist_ok=True)
    # Apaga prints antigos com o nome velho (captcha_*.png)
    for antigo in glob.glob(os.path.join(PASTA_PRINTS, "captcha_*.png")):
        try:
            os.remove(antigo)
        except OSError:
            pass

    caminho = os.path.join(PASTA_PRINTS, NOME_PRINT)
    quadro.screenshot(caminho)  # sobrescreve o imagem.png antigo
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


def processar_resposta(driver):
    """Lê uma resposta e executa. Devolve False quando é para sair."""
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

    clicou = False
    for n in resposta.replace(",", " ").split():
        if n.isdigit() and int(n) in POS_IMAGENS:
            clicar_no_quadro(driver, quadro, *POS_IMAGENS[int(n)])
            time.sleep(0.3)
            print(f"Cliquei na imagem {n}.")
            clicou = True
        else:
            print(f"'{n}' não é válido. Use 1 a 9, v, p ou s.")

    # Depois dos números, clica em Verificar/Próximo sozinho
    if clicou:
        time.sleep(0.5)
        clicar_no_quadro(driver, quadro, *POS_BOTAO)
        print("Cliquei em Verificar. Tirando print da próxima imagem...")
        tirar_print(driver)
    return True


def modo_teclado(driver):
    while True:
        try:
            if not processar_resposta(driver):
                return
        except Exception as erro:
            texto = str(erro).splitlines()[0] if str(erro) else ""
            print(f"Deu erro: {type(erro).__name__}: {texto}")
            print("Tente de novo, ou digite p para tirar um print novo.")


def main():
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
    print(" PAREI NA IMAGEM. Responda pelo teclado:")
    print("   1 a 9      -> clica nas imagens e depois em Verificar (ex.: 1 5 9)")
    print("              1 2 3")
    print("              4 5 6   <- posição das imagens")
    print("              7 8 9")
    print("   v          -> clica no botão Verificar / Pular / Próximo")
    print("   p          -> tira um print novo da imagem")
    print("   s          -> sair (o navegador continua aberto)")
    print("=" * 60)
    modo_teclado(driver)
    print("Robô encerrado. O navegador continua aberto.")


if __name__ == "__main__":
    main()
