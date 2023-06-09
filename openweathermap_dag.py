# Installer et importer ces packages
import requests
import os
import json
import pandas as pd

from airflow import DAG
from airflow.utils.dates import days_ago
from airflow.operators.python import PythonOperator
from datetime import datetime, timedelta
from sklearn.model_selection import cross_val_score
from sklearn.linear_model import LinearRegression
from sklearn.tree import DecisionTreeRegressor
from sklearn.ensemble import RandomForestRegressor
from joblib import dump
from airflow.decorators import task
from airflow.models import Variable
from airflow.sensors.filesystem import FileSensor

# Pour ignorer les warning lors de la copy du dataframe
pd.options.mode.chained_assignment = None  # default='warn'

# Variables à personnaliser par les votres

# Default values
default_api_url="https://api.openweathermap.org/data/2.5/weather"
default_api_key="e8ea6c311663cd96ae4a16bb35ab99de"
default_api_cities='{"1":"paris", "2":"london", "3":"washington"}'

# Custom values
api_url="https://api.openweathermap.org/data/2.5/weather"
api_key="e8ea6c311663cd96ae4a16bb35ab99de"
cities=['paris', 'london', 'washington', 'ouagadougou', 'bamako']

# (1) Récupération de données depuis l'API OpenWeatherMap
def get_weather_data():
    '''
    Permet de recuperer les données de la metéo.
    Cette fonction utilise l'api openweather en prenant la ville comme argument de la requete
    '''
    # Verifie si les variables existent sinon on les crées
    if api_url == "":
        print("L'url de l'api n'est pas definie. La valeur par defaut sera utilisée")
        try:
            if Variable.get("API_URL") != None:
                print("API_URL existe")
            else:
                Variable.set(key="API_URL", value=default_api_url)
        except KeyError:
            Variable.set(key="API_URL", value=default_api_url)

        # Recuperation valeurs des variables
        API_URL = Variable.get("API_URL")
    else:
        print("L'url de l'api est definie")
        API_URL = api_url

    if api_key =="":
        print("La clé de l'api n'est pas definie. La valeur par defaut sera utilisée")
        try:
            if Variable.get("API_TOKEN") != None:
                print("API_TOKEN existe")
            else:
                Variable.set(key="API_TOKEN", value=default_api_key)
        except KeyError:
            Variable.set(key="API_TOKEN", value=default_api_key)

        # Recuperation valeurs des variables
        API_KEY = Variable.get("API_TOKEN")
    else:
        print("La clé de l'api est definie")
        API_KEY = api_key

    
    if len(cities) == 0:
        print("La variable cities n'est pas definie. La valeur par defaut sera utilisée")
        try:
            if Variable.get("API_CITIES") != None:
                print("API_CITIES existe")
            else:
                Variable.set(key="API_CITIES", value=default_api_cities)
        except KeyError:
            Variable.set(key="API_CITIES", value=default_api_cities)

        # Recuperation valeurs des variables
        API_CITIES = list(Variable.get("API_CITIES", deserialize_json=True).values())
    else:
        print("La variable cities est definie")
        API_CITIES = cities

    for city in API_CITIES:
        resp = requests.get(API_URL+'?q='+city+'&appid='+API_KEY)

        with open('/app/raw_files/'+datetime.now().strftime('%Y-%m-%d %H:%M:%S')+'.json', 'a') as file:
            file.write(str(resp.json())+'\n')

# (2) et (3) transformation des données
def transform_data_into_csv(n_files=None, filename='data.csv'):
    '''
    Permet de transformer les données récoltés dans les fichiers json en csv
    Lit un repertoire contenant les fichiers et converti les données au format csv.
    Prend en argument le nombre des derniers fichiers souhaités ainsi que le nom du fichier csv
    '''
    parent_folder = '/app/raw_files'
    files = sorted(os.listdir(parent_folder), reverse=True)
    if n_files:
        files = files[:n_files]

    dfs = []

    for f in files:
        with open(os.path.join(parent_folder, f), 'r') as file:
            filedata = file.read().replace("'", '"')
        with open(os.path.join(parent_folder, f), 'w') as file:
            file.write(filedata)
        with open(os.path.join(parent_folder, f), 'r') as file:
            data_temp = file.readlines()
            #print(data_temp)
        for data_city in data_temp:
            data_city = json.loads(data_city)
            dfs.append(
                {
                    'temperature': data_city['main']['temp'],
                    'city': data_city['name'],
                    'pression': data_city['main']['pressure'],
                    'date': f.split('.')[0]
                }
            )

    df = pd.DataFrame(dfs)

    print('\n', df.head(10))

    df.to_csv(os.path.join('/app/clean_data', filename), index=False)

# (4) et (5) entraînement de modèles et sélection du plus performant
def compute_model_score(model, X, y, task_instance=None, XComs=None):
    '''
    Determine le score du modele et retourne le resultat
    Prend en argument le nom du model, les features, les targets ainsi que le nom de la variable XCom
    '''
    # computing cross val
    cross_validation = cross_val_score(
        model,
        X,
        y,
        cv=3,
        scoring='neg_mean_squared_error')

    model_score = cross_validation.mean()

    task_instance.xcom_push(
        key=XComs,
        value=model_score
    )
    print("Le score est:",model_score)


def train_and_save_model(model, X, y, path_to_model='./app/model.pckl'):
    '''
    Permet d'entrainer le model.
    Prend en argument le model, les fatures, les targets ainsi que le chemin du fichier de sortie
    '''
    # training the model
    model.fit(X, y)
    # saving model
    print(str(model), 'saved at ', path_to_model)
    dump(model, path_to_model)


def prepare_data(path_to_data='/app/clean_data/fulldata.csv'):
    '''
    Prepare les données d'entrainement.
    Prend en argument le fichier de données
    '''
    # reading data
    df = pd.read_csv(path_to_data)
    # ordering data according to city and date
    df = df.sort_values(['city', 'date'], ascending=True)

    dfs = []

    for c in df['city'].unique():
        df_temp = df[df['city'] == c]

        # creating target
        df_temp.loc[:, 'target'] = df_temp['temperature'].shift(1)

        # creating features
        for i in range(1, 10):
            df_temp.loc[:, 'temp_m-{}'.format(i)
                        ] = df_temp['temperature'].shift(-i)

        # deleting null values
        df_temp = df_temp.dropna()

        dfs.append(df_temp)

    # concatenating datasets
    df_final = pd.concat(
        dfs,
        axis=0,
        ignore_index=False
    )

    # deleting date variable
    df_final = df_final.drop(['date'], axis=1)

    # creating dummies for city variable
    df_final = pd.get_dummies(df_final)

    features = df_final.drop(['target'], axis=1)
    target = df_final['target']

    return features, target


def model_comparaison(task_instance=None):
    '''
    Permet de comparer les modeles d'entrainement utilisés
    Prend en argument la tache concerné pour retourne le resultat du model concerné
    '''
    score_lr = task_instance.xcom_pull(
            key="score_lr",
            task_ids=['task_4-1_LinearRegression']
            )

    score_dt = task_instance.xcom_pull(
            key="score_dt",
            task_ids=['task_4-2_DecisionTreeRegressor']
            )

    score_rf = task_instance.xcom_pull(
            key="score_rf",
            task_ids=['task_4-3_RandomForestRegressor']
            )

    if score_lr < score_dt and score_lr < score_rf:
        meilleure_score = score_lr
        train_and_save_model(
            LinearRegression(),
            X,
            y,
            '/app/clean_data/best_model.pickle'
        )
    elif score_dt < score_lr and score_dt < score_rf:
        meilleure_score = score_dt
        train_and_save_model(
            DecisionTreeRegressor(),
            X,
            y,
            '/app/clean_data/best_model.pickle'
        )
    else:
        meilleure_score = score_rf
        train_and_save_model(
            RandomForestRegressor(),
            X,
            y,
            '/app/clean_data/best_model.pickle'
        )

    task_instance.xcom_push(
            key="meilleur_score",
            value=meilleure_score[0]
    )

    print("Le meilleur score est :",meilleure_score[0])


# Initialisation des données préparées (Valeur des features et des targets)
X,y = prepare_data('/app/clean_data/fulldata.csv')


# Definition du DAG

# arguments communs a toutes les tâches
default_args={
	'owner': 'airflow',
    'start_date': days_ago(0, minute=1),
    'trigger_rule':'all_success',
    'pool': 'evaluation_pool'
}

# DAG
my_dag = DAG(
    dag_id='evaluation_airflow_v1',
    description="DAG pour l'evaluation du module airflow",
    doc_md="""
    Ce workflow permet de récupérer des informations depuis une API de données météo disponible en ligne, les stocke, les transforme et entraîne un algorithme.

    Ce DAG permet ainsi de nourrir un dashboard lancé dans un docker-compose.yml dédié et disponible sur le port 8050 de la machine. Ce DAG devra être exécuté toutes les minutes pour mettre à jour régulièrement le dashboard ainsi que le modèle de prédiction.
    """,
    tags=['evaluation'],
    #schedule_interval=None,
    schedule_interval='* * * * *',
    default_args=default_args,
    catchup=False
)

# sensors definition

# sensor1
clean_data_sensor_1 = FileSensor(
    task_id="check_clean_data",
    fs_conn_id="clean_data_fs",
    filepath="data.csv",
    poke_interval=30,
    dag=my_dag,
    timeout=5 * 30,
    mode='reschedule'
)

# sensor2
clean_data_sensor_2 = FileSensor(
    task_id="check_clean_fulldata",
    fs_conn_id="clean_data_fs",
    filepath="fulldata.csv",
    poke_interval=30,
    dag=my_dag,
    timeout=5 * 30,
    mode='reschedule'
)

# sensor3
raw_files_sensor = FileSensor(
    task_id="check_raw_files",
    fs_conn_id="raw_files_fs",
    filepath='*.json',
    poke_interval=30,
    dag=my_dag,
    timeout=5 * 30,
    mode='reschedule'
)

# tasks definition

# task1
my_task_1 = PythonOperator(
    doc = """
    Tâche 1: Récupération de données depuis l'API OpenWeatherMap
    """,
    task_id='task_1_get_weather_data',
    python_callable=get_weather_data,
    retries=5,
    retry_delay=timedelta(seconds=30),
    dag=my_dag
)

# task2
my_task_2 = PythonOperator(
    doc = """
    Tâche 2: Transformation des données. Prend les 20 derniers fichiers du repertoire
    """,
    task_id='task_2_transform_data_into_csv_last_20_files',
    python_callable=transform_data_into_csv,
    dag=my_dag,
    op_kwargs= {
        'n_files': 20,
        'filename': 'data.csv'
    }
)

# task3
my_task_3 = PythonOperator(
    doc = """
    Tâche 3: Transformation des données. Prend tous les fichiers du repertoire
    """,
    task_id='task_3_transform_data_into_csv_all_files',
    python_callable=transform_data_into_csv,
    dag=my_dag,
    op_kwargs= {
        'n_files': None,
        'filename': 'fulldata.csv'
    }
)

# task4'
my_task_4_1 = PythonOperator(
    doc = """
    Tâche 4': Entraînement du modèle LinearRegression
    """,
    task_id='task_4-1_LinearRegression',
    python_callable=compute_model_score,
    dag=my_dag,
    op_kwargs = {
        'model': LinearRegression(),
        'X': X,
        'y': y,
        'XComs': "score_lr"
    }
)

# task4''
my_task_4_2 = PythonOperator(
    doc = """
    Tâche 4'': Entraînement du modèle DecisionTreeRegressor
    """,
    task_id='task_4-2_DecisionTreeRegressor',
    python_callable=compute_model_score,
    dag=my_dag,
    op_kwargs = {
        'model': DecisionTreeRegressor(),
        'X': X,
        'y': y,
        'XComs': "score_dt"
    }
)

# task4'''
my_task_4_3 = PythonOperator(
    doc = """
    Tâche 4''': Entraînement du modèle RandomForestRegressor
    """,
    task_id='task_4-3_RandomForestRegressor',
    python_callable=compute_model_score,
    dag=my_dag,
    op_kwargs = {
        'model': RandomForestRegressor(),
        'X': X,
        'y': y,
        'XComs': "score_rf"
    }
)

# task5
my_task_5 = PythonOperator(
    doc = """
    Tâche 5: Selection du modèle le plus performant et re entraiment sur toutes les données
    """,
    task_id='task_5_ModelComparison',
    python_callable=model_comparaison,
    dag=my_dag
)


# tasks conditions
my_task_1 >> raw_files_sensor >> my_task_2 >> clean_data_sensor_1
my_task_1 >> raw_files_sensor >> my_task_3 >> clean_data_sensor_2
clean_data_sensor_2 >> [my_task_4_1, my_task_4_2, my_task_4_3] >> my_task_5
