#include <stdlib.h>
#include <stdio.h>
#include <string.h>

/*
struct do Discente
o ponteiro "proximo" encadeia os discentes formando uma lista ligada
*/
struct Discente{
	long long int matricula;
	char nome_discente[100];
	int ano_ingresso;
	int periodo_ingresso;
	char tipo_discente[50];
	char status_discente[50];
	char nivel_ensino[50];
	char nome_curso[100];
	char modalidade_educacao[50];
	char nome_unidade[100];
	char nome_unidade_gestora[100];
	struct Discente *proximo;
};

/* imprime na tela todos os campos de um discente */
void imprimirDiscente(const struct Discente *d){
	if (d == NULL){
		printf("Discente invalido ou nulo.\n");
		return;
	}

	printf("========================================");
	printf("\nMatricula:           %lld", d->matricula);
	printf("\nNome:                %s", d->nome_discente);
	printf("\nAno/Periodo:         %d.%d", d->ano_ingresso, d->periodo_ingresso);
	printf("\nNivel de Ensino:     %s", d->nivel_ensino);
	printf("\nCurso:               %s", d->nome_curso);
	printf("\nModalidade:          %s", d->modalidade_educacao);
	printf("\nStatus:              %s", d->status_discente);
	printf("\nTipo:                %s", d->tipo_discente);
	printf("\nUnidade:             %s", d->nome_unidade);
	printf("\nUnidade Gestora:     %s", d->nome_unidade_gestora);
	printf("\n========================================\n");
}

/*
percorre a lista de discentes e grava todos em um arquivo texto,
usando o mesmo formato da funcao de impressao.
*/
void salvarDiscentes(const struct Discente *lista, const char *nomeArquivo){

	/* abre o arquivo em modo escrita */
	FILE *saida = fopen(nomeArquivo, "w");
	if (saida == NULL) {
		printf("Nao foi possivel criar o arquivo de saida.\n");
		return;
	}

	const struct Discente *atual = lista;
	/* percorre a lista do inicio ao fim gravando um discente por vez */
	while (atual != NULL) {
		fprintf(saida, "========================================");
		fprintf(saida, "\nMatricula:           %lld", atual->matricula);
		fprintf(saida, "\nNome:                %s", atual->nome_discente);
		fprintf(saida, "\nAno/Periodo:         %d.%d", atual->ano_ingresso, atual->periodo_ingresso);
		fprintf(saida, "\nNivel de Ensino:     %s", atual->nivel_ensino);
		fprintf(saida, "\nCurso:               %s", atual->nome_curso);
		fprintf(saida, "\nModalidade:          %s", atual->modalidade_educacao);
		fprintf(saida, "\nStatus:              %s", atual->status_discente);
		fprintf(saida, "\nTipo:                %s", atual->tipo_discente);
		fprintf(saida, "\nUnidade:             %s", atual->nome_unidade);
		fprintf(saida, "\nUnidade Gestora:     %s", atual->nome_unidade_gestora);
		fprintf(saida, "\n========================================\n");
		atual = atual->proximo;
	}

	fclose(saida);
}

/*
le o arquivo csv linha a linha, cria um discente para cada linha valida e devolve o inicio da lista ligada
o total de discentes lidos e devolvido pelo parametro "qnt"
 */
struct Discente *lerDiscentesCSV(const char *nomeArquivo, int *qnt, int *descartadas){
	char linha[1024];
	struct Discente *listaDiscentes = NULL;
	struct Discente *atual = NULL;
	
	*descartadas = 0;
	*qnt = 0;

	FILE *arquivoCSV = fopen(nomeArquivo, "r");
	if (arquivoCSV == NULL){
		printf("Arquivo nao encontrado!\n");
    	return NULL;
	}

  	/*le e descarta a primeira linha, que contem o cabecalho*/
  	if (fgets(linha, sizeof(linha), arquivoCSV) == NULL){
    	fclose(arquivoCSV);
    	return NULL;
  	}

  	/* percorre as demais linhas criando um discente para cada uma */
 	while (fgets(linha, sizeof(linha), arquivoCSV) != NULL){
		
		/* remove a quebra de linha */
		linha[strcspn(linha, "\r\n")] = 0;  
		
		/* ignora linhas vazias */
		if (strlen(linha) == 0)            
			continue;

		struct Discente *discenteLido = (struct Discente *)malloc(sizeof(struct Discente));
		if (discenteLido == NULL){
			printf("Erro de alocacao de memoria.\n");
			break;
		}

		int camposLidos = sscanf(linha, "\"%lld\",\"%99[^\"]\",\"%d\",\"%d\",\"%49[^\"]\",\"%49[^\"]\",\"%49[^\"]\",\"%99[^\"]\",\"%49[^\"]\",\"%99[^\"]\",\"%99[^\"]\"",
								&discenteLido->matricula,
								discenteLido->nome_discente,
								&discenteLido->ano_ingresso,
								&discenteLido->periodo_ingresso,
								discenteLido->tipo_discente,
								discenteLido->status_discente,
								discenteLido->nivel_ensino,
								discenteLido->nome_curso,
								discenteLido->modalidade_educacao,
								discenteLido->nome_unidade,
								discenteLido->nome_unidade_gestora);

		discenteLido->proximo = NULL;

		/* descarta a linha se algum campo nao foi lido corretamente */
		if (camposLidos != 11) {
			(*descartadas)++;
			free(discenteLido);
			continue;
		}

		/* insere no fim da lista */
		if (listaDiscentes == NULL)
			listaDiscentes = discenteLido;
		else
			atual->proximo = discenteLido;

		atual = discenteLido;
		(*qnt)++;
  	}

  	fclose(arquivoCSV);
  	return listaDiscentes;
}

int main(){
	int qnt = 0; /* quantidade de discentes lidos do arquivo */
	int descartadas = 0; /* quantidade de linhas descartadas do arquivo */

	/* le o csv e carrega os dados em memoria */
	struct Discente *listaDiscentes = lerDiscentesCSV("dis-csv-discentes-de-graduacao-de-2026.csv", &qnt, &descartadas);

	if (listaDiscentes == NULL){
		printf("Nenhum discente foi carregado do arquivo.\n");
		return 1;
	}

	/* imprime os discentes na tela */
	struct Discente *atual = listaDiscentes;
	while (atual != NULL){
		imprimirDiscente(atual);
		atual = atual->proximo;
	}

	/* salva os mesmos dados em arquivo texto */
	salvarDiscentes(listaDiscentes, "discentes.txt");
	printf("Total de discentes lidos: %d\nLinhas descartadas: %d\n", qnt, descartadas);

	/* libera a memoria */
	while (listaDiscentes != NULL){
		struct Discente *proximo = listaDiscentes->proximo;
		free(listaDiscentes);
		listaDiscentes = proximo;
	}

	return 0;
}