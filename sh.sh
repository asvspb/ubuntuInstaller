#!/bin/bash
#!/bin/bash

echo "Хотите проверить актуальные версии python? (y/n)"
read answer

if [ "$answer" = "y" ]; then
    echo "Проверяем..."
    python ./py-versions.py 
elif [ "$answer" = "n" ]; then
    echo "Хорошо, идем дальше...."
else
    echo "Некорректный ответ. Введите 'y' или 'n'."
fi


