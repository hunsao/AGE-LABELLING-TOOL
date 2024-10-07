import streamlit as st
from PIL import Image
import pandas as pd
from datetime import datetime
import io
import os
import re
import random
import json
import base64

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaFileUpload, HttpRequest
from googleapiclient.errors import HttpError

st.set_page_config(
    page_title="AGEAI Questionnaire",
    page_icon="🧠",
    layout="wide",
    initial_sidebar_state="collapsed"
)

#@st.cache_resource
def get_google_services():
    try:
        # Obtener la cadena codificada de la variable de entorno
        encoded_sa = os.getenv('GOOGLE_SERVICE_ACCOUNT')
        if not encoded_sa:
            raise ValueError("La variable de entorno GOOGLE_SERVICE_ACCOUNT no está configurada")

        # Decodificar la cadena
        sa_json = base64.b64decode(encoded_sa).decode('utf-8')

        # Crear un diccionario a partir de la cadena JSON
        sa_dict = json.loads(sa_json)

        # Crear las credenciales
        credentials = service_account.Credentials.from_service_account_info(
            sa_dict,
            scopes=[
                'https://www.googleapis.com/auth/drive.readonly',
                'https://www.googleapis.com/auth/spreadsheets'
            ]
        )

        # Construir los servicios
        drive_service = build('drive', 'v3', credentials=credentials)
        sheets_service = build('sheets', 'v4', credentials=credentials)

        return drive_service, sheets_service
    except Exception as e:
        st.error(f"Error al obtener los servicios de Google: {str(e)}")
        return None, None

def download_file_from_google_drive(service, file_id):
    try:
        request = service.files().get_media(fileId=file_id)
        fh = io.BytesIO()
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            status, done = downloader.next_chunk()
        return fh.getvalue()
    except Exception as e:
        st.error(f"Error al descargar el archivo: {str(e)}")
        return None

def extract_folder_id(url):
    match = re.search(r'folders/([a-zA-Z0-9-_]+)', url)
    if match:
        return match.group(1)
    return None

def find_images_folder_and_csv_id(service, parent_folder_name):
    try:
        results = service.files().list(
            q=f"name='{parent_folder_name}' and mimeType='application/vnd.google-apps.folder'",
            fields="nextPageToken, files(id)"
        ).execute()
        parent_folders = results.get('files', [])
        if not parent_folders:
            st.error(f"No se encontró la carpeta principal '{parent_folder_name}'.")
            return None, None
        parent_folder_id = parent_folders[0]['id']
        results = service.files().list(
            q=f"'{parent_folder_id}' in parents",
            fields="nextPageToken, files(id, name, mimeType)"
        ).execute()
        items = results.get('files', [])
        images_folder_id = None
        csv_file_id = None
        for item in items:
            if item['name'] == 'IMAGES' and item['mimeType'] == 'application/vnd.google-apps.folder':
                images_folder_id = item['id']
            elif item['name'].endswith('.csv') and item['mimeType'] == 'text/csv':
                csv_file_id = item['id']
        if not images_folder_id:
            st.error("No se encontró la carpeta 'IMAGES'.")
        if not csv_file_id:
            st.error("No se encontró el archivo CSV.")
        return images_folder_id, csv_file_id
    except Exception as e:
        st.error(f"Error al buscar la carpeta 'IMAGES' y el CSV: {str(e)}")
        return None, None

@st.cache_data()
def list_images_in_folder(_service, folder_id):
    try:
        results = _service.files().list(
            q=f"'{folder_id}' in parents and mimeType contains 'image/'",
            fields="nextPageToken, files(id, name)"
        ).execute()
        items = results.get('files', [])
        return items
    except Exception as e:
        st.error(f"Error al listar las imágenes: {str(e)}")
        return []

@st.cache_data()
def download_and_cache_csv(_service, file_id):
    csv_bytes = download_file_from_google_drive(_service, file_id)
    if csv_bytes:
        return pd.read_csv(io.BytesIO(csv_bytes))
    else:
        return None

def save_labels_to_google_sheets(sheets_service, spreadsheet_id, user_id, image_responses):
    try:
        current_datetime = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        # Crear una lista de valores para cada respuesta, incluyendo la pregunta
        values = []
        for image_id, response_dict in image_responses.items():
            # Obtener el nombre de la imagen usando su ID
            image_name = next((img['name'] for img in st.session_state.all_images if img['id'] == image_id), "Unknown Image")
            for question, answer in response_dict.items():
                values.append([user_id, image_name, current_datetime, question, answer])
        
        body = {
            'values': values
        }
        
        result = sheets_service.spreadsheets().values().append(
            spreadsheetId=spreadsheet_id,
            range='Sheet1',
            valueInputOption='USER_ENTERED',
            body=body
        ).execute()

        st.sidebar.success(f'Respuestas guardadas para las imágenes en Google Sheets')
    except Exception as e:
        st.error(f"Error al guardar las etiquetas en Google Sheets: {str(e)}")

# Define the questionnaire structure
# Updated questionnaire structure
questionnaire = {
    "ROUND 1": [
        {
            "question": "Q1. Indicate if the activity you see in the image correlates with the activity formulated in the prompt",
            "options": ["Yes", "No", "Don't know"],
            "definition": """The activity you see in the image is the main action people you observe is doing. For example: eating, walking, speaking...

Options:
Yes = the image is not in contradiction to the activity formulated in the prompt.
No = the image is clearly not the activity formulated in the prompt. 
Don't know = you can't decide if the activity is the same in both cases (prompt and image).""",
            "multiple": False
        }
    ],
    "ROUND 2": [
        {
            "question": "Q5. Indicate what assistive object you appreciate in the image (choose one or more)",
            "options": ["Wheelchairs", "Glasses (but not sun glasses)", "Prosthetic limbs", "White canes", "Others", "None"],
            "definition": "Assistive products can range from physical products such as wheelchairs, glasses, prosthetic limbs, white canes, and hearing aids to digital solutions such as speech recognition or time management software and captioning",
            "multiple": True,
            "other_field": True
        }
    ],
    "ROUND 3": [
        {
            "question": "Q8. Select the characteristics that best describe this images",
            "options": {
                "Attitude": ["Positive attitude", "Negative attitude"],
                "Role": ["Active role", "Passive role"],
                "Physics": ["Physically active", "Physical limitations"],
                "Style": ["Modern style", "Old style"],
                "Other": []
            },
            "definition": "Characteristics refers to the person you see in the image (attitude, role, physics) and to the person/background surrounding them (For example: clothes, walls with memories, etc).",
            "explanation": {
                "Positive attitude": "The person is depicted relaxed, happy, or carefree",
                "Negative attitude": "The person is depicted worried, sad or concerned",
                "Active role": "The person has actively performing the activity of the prompt",
                "Passive role": "The person is passively disengaged from the activity of the prompt",
                "Physically active": "The person exhibits no physical limitations in doing certain activities",
                "Physical limitations": "The person shows physical limitations in doing certain activities",
                "Modern style": "The person is depicted in a stereotypical young style",
                "Old style": "The person is depicted in a stereotypical old style"
            },
            "multiple": True,
            "requires_explanation": True
        }
    ]
}

def display_question(question, current_image_id):
    st.write("### **Question:**")
    st.write(question['question'])
    st.write("### **Definition:**")
    st.write(question['definition'])
    
    responses = {}
    
    if isinstance(question['options'], dict):
        # Handle nested options (Round 3)
        for category, options in question['options'].items():
            st.write(f"#### {category}")
            if options:
                for option in options:
                    col1, col2 = st.columns([1, 3])
                    with col1:
                        selected = st.checkbox(option, key=f"{current_image_id}_{category}_{option}")
                    with col2:
                        if selected and question.get('requires_explanation'):
                            explanation = st.text_area(f"Why {option}?", key=f"{current_image_id}_{option}_explanation")
                            responses[f"{option}_explanation"] = explanation
                    if selected:
                        responses[option] = True
            
            if category == "Other":
                other = st.text_input("Other characteristic:", key=f"{current_image_id}_other")
                if other:
                    explanation = st.text_area("Why?", key=f"{current_image_id}_other_explanation")
                    responses["other"] = other
                    responses["other_explanation"] = explanation
    else:
        # Handle simple options (Round 1 & 2)
        for option in question['options']:
            if option == "Others" and question.get('other_field'):
                selected = st.checkbox(option, key=f"{current_image_id}_{option}")
                if selected:
                    other_text = st.text_input("Please specify:", key=f"{current_image_id}_other_text")
                    responses[option] = other_text
            else:
                selected = st.checkbox(option, key=f"{current_image_id}_{option}")
                if selected:
                    responses[option] = True
    
    return responses

N_IMAGES_PER_QUESTION = 3  # Número de imágenes a mostrar por cada pregunta

def main():
    #st.set_page_config(layout="wide")

    drive_service, sheets_service = get_google_services()
    
    if not drive_service or not sheets_service:
        st.error("No se pudieron obtener los servicios de Google.")
        return

    drive_url = "https://drive.google.com/drive/u/0/folders/1ii7UIuwg2zhoTNytADMIfW9QKRgg51Bs"
    parent_folder_name = "09_20_LABELLING_TEST"
    spreadsheet_id = "10HgyUYfkiS8LuXXRTTHcO9IzglwTXb6DU7Yu_m9z7yE"

    parent_folder_id = extract_folder_id(drive_url)

    if 'page' not in st.session_state:
        st.session_state.page = 'start'

    if 'current_question' not in st.session_state:
        st.session_state.current_question = 0

    if 'responses' not in st.session_state:
        st.session_state.responses = {}

    if 'user_id' not in st.session_state:
        st.session_state.user_id = ''

    if 'review_mode' not in st.session_state:
        st.session_state.review_mode = False    

    if 'current_image_index' not in st.session_state:
        st.session_state.current_image_index = 0

    if 'random_images' not in st.session_state:
        st.session_state.random_images = []

    if 'image_responses' not in st.session_state:
        st.session_state.image_responses = {}

    if 'all_images' not in st.session_state:
        st.session_state.all_images = []

    # Sidebar
    if parent_folder_id:
        images_folder_id, csv_file_id = find_images_folder_and_csv_id(drive_service, parent_folder_name)
        if images_folder_id and csv_file_id:
            image_list = list_images_in_folder(drive_service, images_folder_id)

            if not st.session_state.random_images:
                st.session_state.random_images = random.sample(image_list, N_IMAGES_PER_QUESTION)
                st.session_state.all_images.extend(st.session_state.random_images)  # Guardar todas las imágenes utilizadas

            if st.session_state.page == 'start':
                col1, col2, col3 = st.columns([1, 2, 1])

                with col2:
                    st.markdown("<h1 style='text-align: center;'>Welcome to the AGEAI project questionary</h1>", unsafe_allow_html=True)
                    st.markdown("<p style='text-align: center;'>This tool is designed to help us collect data about images created with AI.</p>", unsafe_allow_html=True)
                    st.markdown("<p style='text-align: center;'>You will be presented with a series of images and questions. Please answer them to the best of your ability.</p>", unsafe_allow_html=True)
                    st.markdown("<p style='text-align: center;'>Your responses are valuable and will contribute to the improving our findings.</p>", unsafe_allow_html=True)
                    
                    st.session_state.user_id = st.text_input('Enter your user ID', value=st.session_state.user_id)
                    
                    if st.session_state.user_id:
                        if st.button("Start Questionnaire"):
                            st.session_state.page = 'questionnaire'
                            st.rerun()
                    else:
                        st.warning("Please enter an user ID and click to start the questionnaire.")

            elif st.session_state.page == 'questionnaire':
                # Update the sidebar progress display
                total_questions = sum(len(questions) for questions in questionnaire.values())
                current_round = list(questionnaire.keys())[st.session_state.current_question // len(questionnaire)]
                
                # Mostrar progreso en la barra lateral
                for round_name, questions in questionnaire.items():
                    st.sidebar.subheader(round_name)
                    for i, q in enumerate(questions):
                        question_number = i + 1 if round_name == "ROUND 1" else len(questionnaire["ROUND 1"]) + i + 1
                        if st.session_state.review_mode or question_number <= st.session_state.current_question:
                            if st.sidebar.button(f"✅ {q['question'][:100]}...", key=f"nav_{round_name}_{i}"):
                                st.session_state.current_question = question_number - 1
                                st.rerun()
                        else:
                            st.sidebar.button(f"⬜ {q['question'][:100]}...", key=f"nav_{round_name}_{i}", disabled=True)

                # Contenido principal
                col1, col2 = st.columns([2, 3])

                with col2:
                    # Mostrar la imagen actual almacenada en session_state
                    current_image = st.session_state.random_images[st.session_state.current_image_index]
                    image_bytes = download_file_from_google_drive(drive_service, current_image['id'])
                    st.image(image_bytes, use_column_width=True)

                with col1:
                    current_round = "ROUND 1" if st.session_state.current_question < len(questionnaire["ROUND 1"]) else "ROUND 2"
                    current_question = questionnaire[current_round][st.session_state.current_question % len(questionnaire[current_round])]

                    #st.title(f"{current_round}")
                    #st.markdown(f"<h1 style='text-align: center; color: red;'>{current_round}</h1>",unsafe_allow_html=True)
                    st.write("### **Question:**")
                    st.write(current_question['question'])
                    st.write("### **Definition:**")
                    st.write(current_question['definition'])

                    # Guardar respuesta para cada imagen y pregunta
                    default_answer = st.session_state.responses.get(f"{current_image['id']}_{current_question['question']}")

                    try:
                        if "options" in current_question and current_question["options"]:
                            options = current_question["options"]
                            if default_answer is not None and default_answer in options:
                                index = options.index(default_answer)
                            else:
                                index = 0
                            
                            answer = st.radio(
                                "Select an option:", 
                                options, 
                                key=f"question_{st.session_state.current_question}_{st.session_state.current_image_index}", 
                                index=index
                            )
                        else:
                            st.error("No options available for this question.")
                            answer = None
                    except Exception as e:
                        st.error(f"An error occurred: {str(e)}")
                        answer = None

                    # Botones de navegación y botón de siguiente pregunta
                    col1, col2, col3 = st.columns([1, 3, 1])

                    with col1:
                        if st.button("Previous image") and st.session_state.current_image_index > 0:
                            st.session_state.current_image_index -= 1
                            st.rerun()

                    # with col2:
                    #     st.write(f"Current image: {st.session_state.current_image_index + 1} de {N_IMAGES_PER_QUESTION}")
                    with col2:
                        st.write("<div style='text-align: center;'>Current image: 1 de 2</div>", unsafe_allow_html=True)
                    
                    with col3:
                        if st.button("Next image") and st.session_state.current_image_index < N_IMAGES_PER_QUESTION - 1:
                            # Guardar la respuesta actual antes de cambiar de imagen
                            if answer is not None:
                                current_image_id = st.session_state.random_images[st.session_state.current_image_index]['id']
                                if current_image_id not in st.session_state.image_responses:
                                    st.session_state.image_responses[current_image_id] = {}
                                st.session_state.image_responses[current_image_id][current_question["question"]] = answer
                                st.session_state.responses[f"{current_image_id}_{current_question['question']}"] = answer
                            st.session_state.current_image_index += 1
                            st.rerun()

                    with col2:
                        st.markdown(
                            """
                            <style>
                            div.stButton > button {
                                display: block;
                                margin: 0 auto;
                            }
                            </style>
                            """,
                            unsafe_allow_html=True
                        )
                        if st.button("Next Question", key="next_button"):
                            if answer is not None:
                                # Guardar la respuesta de la imagen actual antes de pasar a la siguiente pregunta
                                current_image_id = st.session_state.random_images[st.session_state.current_image_index]['id']
                                if current_image_id not in st.session_state.image_responses:
                                    st.session_state.image_responses[current_image_id] = {}
                                st.session_state.image_responses[current_image_id][current_question["question"]] = answer
    
                                # Guardar la respuesta con identificador único para imagen y pregunta
                                st.session_state.responses[f"{current_image_id}_{current_question['question']}"] = answer
                                st.session_state.current_question += 1
                                if st.session_state.current_question >= len(questionnaire["ROUND 1"]) + len(questionnaire["ROUND 2"]):
                                    st.session_state.page = 'review'
                                    st.session_state.review_mode = True
                                else:
                                    # Seleccionar N nuevas imágenes aleatorias solo si se avanza a una nueva pregunta
                                    st.session_state.random_images = random.sample(image_list, N_IMAGES_PER_QUESTION)
                                    st.session_state.all_images.extend(st.session_state.random_images)  # Guardar todas las imágenes utilizadas
                                    st.session_state.current_image_index = 0
                                st.rerun()
                            else:
                                st.warning("Please select an answer before proceeding.")

            elif st.session_state.page == 'review':
                st.title("Cuestionario completado")
                st.write("Has completado todas las preguntas. Puedes revisar tus respuestas o enviar el cuestionario.")

                if st.button("Revisar respuestas"):
                    st.session_state.current_question = 0
                    st.session_state.page = 'questionnaire'
                    st.session_state.review_mode = True
                    st.rerun()

                if st.button("Enviar cuestionario"):
                    # Guardar las respuestas en Google Sheets
                    save_labels_to_google_sheets(
                        sheets_service, 
                        spreadsheet_id, 
                        st.session_state.user_id, 
                        st.session_state.image_responses
                    )

                    st.session_state.page = 'end'
                    st.session_state.review_mode = False
                    
                    # Limpiar caché y session_state relacionado con las imágenes
                    st.cache_data.clear()
                    del st.session_state['random_images']
                    del st.session_state['current_image_index']
                    del st.session_state['image_responses']
                    del st.session_state['all_images']

                    st.rerun()

            elif st.session_state.page == 'end':
                st.title("Thanks for participating! 😊")
                st.balloons()
                st.write("Your responses have been saved and will be used to improve our AI systems.")
                st.write("We appreciate your time and effort in completing this questionnaire.")
                if st.button("Start New Questionnaire"):
                    st.session_state.current_question = 0
                    st.session_state.responses = {}
                    st.session_state.page = 'start'
                    st.session_state.user_id = ''
                    st.session_state.review_mode = False
                    st.rerun()

    else:
        st.error("No se pudo obtener el ID de la carpeta principal.")



if __name__ == "__main__":
    main()
