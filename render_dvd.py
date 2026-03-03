import os
import requests
import time

def render_dvd(input_folder, output_folder, file_names):
    """
    Render MP4 files to DVD format using the DVD Author API.
    
    Args:
        input_folder (str): Path to folder containing MP4 files
        output_folder (str): Path where DVD files should be output
    """
    # API endpoint
    api_url = "http://10.0.0.54:5000/run-dvd-author"
    
    # Request payload
    payload = {
        "input_folder": input_folder,
        "output_folder": output_folder,
        "file_names": file_names
    }
    
    try:
        # Make API request
        response = requests.post(api_url, json=payload)
        response.raise_for_status()
        
        print(f"DVD render job started. Monitoring {output_folder}/temp for completion...")
        
        # Monitor temp folder for completion
        temp_folder = os.path.join(output_folder, "temp")
        while True:
            if not os.path.exists(temp_folder):
                print("DVD render completed successfully")
                break
            print("Render still in progress... waiting 30 seconds")
            time.sleep(30)
            
    except requests.exceptions.RequestException as e:
        print(f"Error making API request: {str(e)}")
    except Exception as e:
        print(f"Unexpected error: {str(e)}")

if __name__ == "__main__":
    # Example usage
    input_folder = "/7 - Transferring/162167/CINE"
    output_folder = "/7 - Transferring/162167/DVD"
    file_names = ["152217T2V1.mp4"]
    render_dvd(input_folder, output_folder, file_names)
