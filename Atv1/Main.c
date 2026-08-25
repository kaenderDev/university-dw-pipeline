	#include <stdlib.h>
	#include <stdio.h>
	#include <string.h>

	struct Discente
	{
	  long long int matricula;
	  char nome_discente[100];
	  int ano_ingresso;
	  int periodo_ingresso;
	  char tipo_discente[20];
	  char status_discente[20];
	  char nivel_ensino[20];
	  char nome_curso[100];
	  char modalidade_educacao[20];
	  char nome_unidade[100];
	  char nome_unidade_gestora[100];
	  struct Discente *proximo;
	};

	void imprimirDiscente(const struct Discente *d)
	{
	  if (d == NULL)
	  {
		printf("Discente invalido ou nulo.\n");
		return;
	  }

	  printf("========================================");
	  printf("\nMatricula:           %lld", d->matricula);
	  printf("\nNome:                %s", d->nome_discente);
	  printf("\nAno/Periodo:         %d.%d", d->ano_ingresso, d->periodo_ingresso);
	  printf("\nCurso:               %s", d->nome_curso);
	  printf("\nStatus:              %s", d->status_discente);
	  printf("\nTipo:                %s", d->tipo_discente);
	  printf("\nUnidade:             %s", d->nome_unidade);
	  printf("\nUnidade Gestora:     %s", d->nome_unidade_gestora);
	  printf("\n========================================\n");
	}

	int main()
	{
	  FILE *arquivoCSV = fopen("dis-csv-discentes-de-graduacao-de-2026.csv", "r");
	  char linha[1024];
	  struct Discente *listaDiscentes = NULL;
	  struct Discente *atual = NULL;
	  int qnt = 0;
	  if (arquivoCSV == NULL)
	  {
		printf("Arquivo nao encontrado!\n");
		return 1;
	  }

	  while (fgets(linha, sizeof(linha), arquivoCSV) != NULL)
	  {

		// remove a quebra de linha
		linha[strcspn(linha, "\r\n")] = 0;
		// ignora linhas vazias
		if (strlen(linha) == 0)
		  continue;

		struct Discente *discenteLido = (struct Discente *)malloc(sizeof(struct Discente));
		int camposLidos = sscanf(linha,  "\"%lld\",\"%99[^\"]\",\"%d\",\"%d\",\"%99[^\"]\",\"%99[^\"]\",\"%99[^\"]\",\"%99[^\"]\",\"%99[^\"]\",\"%99[^\"]\",\"%99[^\"]\"",
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
		
		if (camposLidos != 11) {
		free(discenteLido);
		continue;
		}
		
		if (listaDiscentes == NULL)
		{
		  listaDiscentes = discenteLido;
		  atual = discenteLido;
		  qnt++;
		}
		else
		{
		  atual->proximo = discenteLido;
		  atual = discenteLido;
		  qnt++;
		}
	  }

	  fclose(arquivoCSV);

	  atual = listaDiscentes;
	  if (atual == NULL) {
			printf("Nenhum discente foi carregado do arquivo.\n");}
	  while (atual->proximo != NULL){
		imprimirDiscente(atual);
		atual = atual->proximo;
	  }
	  printf("%d", qnt);
	  return 0;
	}
