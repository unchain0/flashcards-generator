usage: flashcards merge [-h] --folder FOLDER [--output OUTPUT] [--deduplicate]
                        [--no-recursive]

options:
  -h, --help            show this help message and exit
  --folder FOLDER, -f FOLDER
                        Pasta contendo arquivos CSV para mesclar
  --output OUTPUT, -o OUTPUT
                        Nome do arquivo de saída (padrão:
                        merged_flashcards.csv)
  --deduplicate, -d     Remover flashcards duplicados durante a mescla
  --no-recursive        Não buscar em subpastas (padrão: busca recursiva)
