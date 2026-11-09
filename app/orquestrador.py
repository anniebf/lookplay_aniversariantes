"""
Fluxo de uma execução, a parte central do projeto:

    consulta o Oracle -> para cada filial e tipo:
        sem aniversariantes -> registra e não envia
        com aniversariantes -> gera o CSV e envia ao Lookplay

Diferença para o script antigo: o banco é consultado ANTES de qualquer acesso ao
Lookplay. Assim, "sem aniversariantes" e "sem conteúdo cadastrado" passam a ser
situações distintas, que antes apareciam juntas no mesmo aviso do log.

As dependências chegam pelo construtor. O mesmo código roda com Oracle e Lookplay
reais, com o modo exemplo ou com os dublês dos testes.
"""
import logging
import threading
import time

from app.cliente_lookplay import ConteudoNaoEncontrado, ErroLookplay, SessaoInvalida
from app.dominio import Tipo, gerar_csv, nome_do_csv, resumo_do_envio
from app.historico import Historico

logger = logging.getLogger(__name__)

NOMES_NO_LOG = {Tipo.VIDA: "Vida", Tipo.CASA: "Empresa"}


def tipos_da_escolha(escolha: str) -> list[Tipo]:
    return [Tipo.CASA, Tipo.VIDA] if escolha == "ambos" else [Tipo(escolha)]


class Orquestrador:
    def __init__(self, repositorio, fabrica_cliente, historico: Historico, pausa: float = 0.5, ao_terminar=None):
        self._repositorio = repositorio
        # Fábrica: cada execução recebe um cliente, com a sua própria sessão HTTP.
        self._fabrica_cliente = fabrica_cliente
        self._historico = historico
        self._pausa = pausa
        self._ao_terminar = ao_terminar

    def iniciar_em_segundo_plano(self, escolha: str, filial: str | None, origem: str) -> int:
        """Registra a execução e roda em outra thread, para a API responder na hora."""
        execucao_id = self._historico.criar_execucao(origem, escolha, filial)
        threading.Thread(target=self.executar, args=(execucao_id, escolha, filial), daemon=True).start()
        return execucao_id

    def executar_agora(self, escolha: str = "ambos", filial: str | None = None, origem: str = "agendador") -> int:
        """Roda até o fim no processo atual. Usado pelo Agendador de Tarefas."""
        execucao_id = self._historico.criar_execucao(origem, escolha, filial)
        self.executar(execucao_id, escolha, filial)
        return execucao_id

    def executar(self, execucao_id: int, escolha: str, filial: str | None) -> None:
        try:
            erros, mensagem = self._processar(execucao_id, tipos_da_escolha(escolha), filial)
            status = "concluida_com_erros" if erros else "concluida"
            self._historico.registrar_evento(execucao_id, "Execução concluída com pendências." if erros else "Execução concluída.")
            self._historico.finalizar(execucao_id, status, mensagem)
        except Exception as erro:  # ex.: Oracle fora do ar
            # A mensagem do driver pode conter dados de conexão: o detalhe fica só no log.
            logger.exception("Execução %s falhou", execucao_id)
            self._historico.registrar_evento(execucao_id, "A execução falhou. Veja o log do servidor.")
            self._historico.finalizar(execucao_id, "falhou", f"A execução parou: {type(erro).__name__}. Veja o log.")
        except BaseException:  # Ctrl+C: registra antes de sair, para não bloquear as próximas execuções
            self._historico.registrar_evento(execucao_id, "A execução foi interrompida pelo usuário.")
            self._historico.finalizar(execucao_id, "interrompida", "A execução foi interrompida pelo usuário.")
            raise
        finally:
            if self._ao_terminar:
                self._ao_terminar()

    def _processar(self, execucao_id: int, tipos: list[Tipo], filial: str | None) -> tuple[int, str | None]:
        # Passo 1: consultar antes de abrir sessão. Sem aniversariantes, o Lookplay nem é acessado.
        self._historico.registrar_evento(execucao_id, "Consultando os aniversariantes no banco de dados.")
        filiais = [filial] if filial else self._repositorio.filiais()
        consultas = {tipo: self._repositorio.aniversariantes(tipo) for tipo in tipos}

        cliente = None
        sessao_erro = None
        erros = 0

        for tipo in tipos:
            nome_tipo = NOMES_NO_LOG[tipo]
            for nome_filial in filiais:
                pessoas = consultas[tipo].get(nome_filial, [])

                # Passo 2: sem aniversariantes, só registra.
                if not pessoas:
                    self._historico.registrar_item(execucao_id, nome_filial, tipo.value, "sem_aniversariantes", 0)
                    self._historico.registrar_evento(execucao_id, f"{nome_tipo}: sem aniversariantes, nada a enviar.", nome_filial)
                    continue

                # Passo 3: sem sessão, as demais filiais nem tentam enviar.
                if sessao_erro:
                    erros += 1
                    self._historico.registrar_item(execucao_id, nome_filial, tipo.value, "erro", len(pessoas), sessao_erro)
                    self._historico.registrar_evento(execucao_id, f"{nome_tipo}: não enviado. {sessao_erro}", nome_filial)
                    continue

                # Passo 4: abre a sessão só na primeira filial que precisar.
                try:
                    if cliente is None:
                        self._historico.registrar_evento(execucao_id, "Abrindo a sessão no Lookplay.", nome_filial)
                        cliente = self._fabrica_cliente()
                        cliente.abrir_sessao()
                    self._historico.registrar_evento(execucao_id, f"{nome_tipo}: enviando o CSV com {len(pessoas)} pessoa(s).", nome_filial)
                    cliente.enviar_csv(tipo, nome_filial, nome_do_csv(nome_filial), gerar_csv(pessoas))
                    self._historico.registrar_item(
                        execucao_id, nome_filial, tipo.value, "enviado", len(pessoas), pessoas=resumo_do_envio(pessoas, tipo)
                    )
                    self._historico.registrar_evento(execucao_id, f"{nome_tipo}: arquivo enviado e confirmado no Lookplay.", nome_filial)
                    time.sleep(self._pausa)  # não sobrecarrega o Lookplay
                except ConteudoNaoEncontrado as erro:
                    erros += 1
                    self._historico.registrar_item(execucao_id, nome_filial, tipo.value, "sem_conteudo", len(pessoas), str(erro))
                    self._historico.registrar_evento(execucao_id, f"{nome_tipo}: filial sem conteúdo no Lookplay, não enviado.", nome_filial)
                except SessaoInvalida as erro:
                    erros += 1
                    sessao_erro = str(erro)
                    self._historico.registrar_item(execucao_id, nome_filial, tipo.value, "erro", len(pessoas), sessao_erro)
                    self._historico.registrar_evento(execucao_id, f"{nome_tipo}: erro. {sessao_erro}", nome_filial)
                except (ErroLookplay, OSError) as erro:
                    # OSError cobre falhas de rede do requests. Uma filial não derruba as outras.
                    erros += 1
                    logger.warning("Falha ao enviar %s de %s: %s", tipo.value, nome_filial, erro)
                    self._historico.registrar_item(execucao_id, nome_filial, tipo.value, "erro", len(pessoas), str(erro)[:300])
                    self._historico.registrar_evento(execucao_id, f"{nome_tipo}: erro. {str(erro)[:300]}", nome_filial)

        return erros, sessao_erro
