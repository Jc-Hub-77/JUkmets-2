import os
import config
import json
import shutil # For moving directories
import logging # Added logging

logger = logging.getLogger(__name__)

ITEMS_BASE_DIR = config.ITEMS_BASE_DIR
PURCHASED_ITEMS_BASE_DIR = config.PURCHASED_ITEMS_BASE_DIR

def get_cities():
    """Lists directories within ITEMS_BASE_DIR, representing cities."""
    try:
        if not os.path.exists(ITEMS_BASE_DIR):
            os.makedirs(ITEMS_BASE_DIR)
            logger.info(f"Created ITEMS_BASE_DIR at {ITEMS_BASE_DIR}")
        if not os.path.exists(PURCHASED_ITEMS_BASE_DIR):
            os.makedirs(PURCHASED_ITEMS_BASE_DIR)
            logger.info(f"Created PURCHASED_ITEMS_BASE_DIR at {PURCHASED_ITEMS_BASE_DIR}")
    except OSError as e:
        logger.exception(f"Error creating base directories: {e}")
        return [] # Return empty if base dirs can't be made

    cities = []
    try:
        if os.path.isdir(ITEMS_BASE_DIR):
            for entry in os.listdir(ITEMS_BASE_DIR):
                if os.path.isdir(os.path.join(ITEMS_BASE_DIR, entry)):
                    cities.append(entry)
    except OSError as e:
        logger.exception(f"Error listing cities in {ITEMS_BASE_DIR}: {e}")
        return [] # Return empty on error
    return cities

def get_items_in_city(city_name):
    """Lists directories within a specific city, representing product types."""
    city_path = os.path.join(ITEMS_BASE_DIR, city_name)
    items = []
    if not os.path.isdir(city_path): # Check if city_path is a directory first
        logger.warning(f"City path not found or not a directory: {city_path}")
        return items # Return empty list

    try:
        for entry in os.listdir(city_path):
            if os.path.isdir(os.path.join(city_path, entry)):
                items.append(entry)
    except OSError as e:
        logger.exception(f"Error listing items in city {city_name} at {city_path}: {e}")
        return [] # Return empty on error
    return items

def get_product_instances(product_folder_path: str) -> list[str]:
    """
    Lists all subdirectories within product_folder_path, sorted alphabetically.
    These subdirectories are considered "instances" of a product.
    """
    if not os.path.isdir(product_folder_path):
        logger.warning(f"Product folder not found or not a directory: {product_folder_path}")
        return []

    instances = []
    try:
        for entry in os.listdir(product_folder_path):
            if os.path.isdir(os.path.join(product_folder_path, entry)):
                instances.append(entry)
        instances.sort()
    except OSError as e:
        logger.exception(f"Error listing product instances in {product_folder_path}: {e}")
        return []
    return instances

def get_instance_details(instance_folder_path: str) -> dict | None:
    """
    Retrieves details of a specific product instance.
    Details include description and paths to up to 3 image files.
    """
    if not os.path.isdir(instance_folder_path):
        logger.warning(f"Instance folder not found or not a directory: {instance_folder_path}")
        return None

    details = {'description': '', 'image_paths': []}
    desc_path = os.path.join(instance_folder_path, 'description.txt')

    if os.path.exists(desc_path):
        try:
            with open(desc_path, 'r', encoding='utf-8') as f:
                details['description'] = f.read().strip()
        except Exception as e_read:
            logger.exception(f"Error reading description file at {desc_path}: {e_read}")
            details['description'] = "Error reading description."
            # As per original plan, if desc.txt is missing, return None.
            # If it exists but can't be read, this is an error state for the instance.
            # Depending on strictness, could return None here too.
            # For now, proceed with error in description. If plan implies None on read error, change this.
            # The plan stated: "If not found, returns None". It's found but unreadable.
            # Let's adjust to return None if description cannot be read, as it's essential.
            return None
    else:
        logger.warning(f"Description file not found for instance: {instance_folder_path}")
        return None # Adhering to "If not found, returns None"

    image_extensions = ('.jpg', '.jpeg', '.png')
    image_count = 0
    try:
        for entry in os.listdir(instance_folder_path):
            if entry.lower().endswith(image_extensions) and image_count < 3:
                details['image_paths'].append(os.path.join(instance_folder_path, entry))
                image_count += 1
    except OSError as e_list_img:
        logger.exception(f"Error listing images in instance folder {instance_folder_path}: {e_list_img}")
        # If images cannot be listed, this might still be a valid instance if description is primary.
        # For now, return details obtained so far. If images are critical, return None.
        pass # Continue with what we have

    return details


def get_item_details(city_name: str, product_type_name: str) -> dict | None:
    """
    Gets display details for a product type by using its oldest available instance.
    """
    product_folder_path = os.path.join(ITEMS_BASE_DIR, city_name, product_type_name)
    if not os.path.isdir(product_folder_path):
        logger.warning(f"Product type folder not found for details: {product_folder_path}")
        return None

    instance_names = get_product_instances(product_folder_path)
    if not instance_names:
        logger.info(f"No instances found for product type {product_type_name} in {city_name} at {product_folder_path}")
        return None

    oldest_instance_name = instance_names[0]
    oldest_instance_path = os.path.join(product_folder_path, oldest_instance_name)
    logger.debug(f"Fetching details from oldest instance: {oldest_instance_path} for product type {product_type_name}")

    instance_details = get_instance_details(oldest_instance_path)

    if instance_details:
        instance_details['actual_instance_path'] = oldest_instance_path
        return instance_details

    logger.warning(f"Could not get details from oldest instance {oldest_instance_path} for product type {product_type_name}")
    return None


def move_item_to_purchased(city_name: str, product_type_name: str, instance_folder_name: str) -> bool:
    """Moves a specific item instance folder to the purchased items directory."""
    source_instance_path = os.path.join(ITEMS_BASE_DIR, city_name, product_type_name, instance_folder_name)
    destination_product_type_path = os.path.join(PURCHASED_ITEMS_BASE_DIR, city_name, product_type_name)
    destination_instance_path = os.path.join(destination_product_type_path, instance_folder_name)

    if not os.path.exists(source_instance_path):
        logger.error(f"Instance '{instance_folder_name}' for product '{product_type_name}' in city '{city_name}' not found for moving (path: {source_instance_path}).")
        return False

    if not os.path.exists(destination_product_type_path):
        try:
            os.makedirs(destination_product_type_path)
            logger.info(f"Created destination product type directory: {destination_product_type_path}")
        except OSError as e:
            logger.exception(f"Error creating destination directory '{destination_product_type_path}': {e}")
            return False

    try:
        shutil.move(source_instance_path, destination_instance_path)
        logger.info(f"Item instance '{instance_folder_name}' moved from '{source_instance_path}' to '{destination_instance_path}'.")

        try:
            source_product_type_path = os.path.join(ITEMS_BASE_DIR, city_name, product_type_name)
            if not os.listdir(source_product_type_path): # Check if product type folder is empty
                os.rmdir(source_product_type_path)
                logger.info(f"Removed empty product type folder: {source_product_type_path}")

                source_city_path = os.path.join(ITEMS_BASE_DIR, city_name)
                if not os.listdir(source_city_path): # Check if city folder is empty
                     os.rmdir(source_city_path)
                     logger.info(f"Removed empty city folder: {source_city_path}")
        except OSError as e_rm:
            logger.warning(f"Could not remove empty parent directories for {product_type_name} in {city_name} after move: {e_rm}. This is non-critical.")
            pass
        return True
    except Exception as e:
        logger.exception(f"Error moving item instance '{instance_folder_name}' from {source_instance_path} to {destination_instance_path}: {e}")
        return False

if __name__ == '__main__':
    # This block is for direct testing, print statements are fine here.
    # Setup logging for __main__ to see output from the functions if they are called.
    logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

    if os.path.exists(config.ITEMS_BASE_DIR):
        shutil.rmtree(config.ITEMS_BASE_DIR)
    if os.path.exists(config.PURCHASED_ITEMS_BASE_DIR):
        shutil.rmtree(config.PURCHASED_ITEMS_BASE_DIR)

    os.makedirs(config.ITEMS_BASE_DIR, exist_ok=True)
    os.makedirs(config.PURCHASED_ITEMS_BASE_DIR, exist_ok=True)

    city_a_path = os.path.join(config.ITEMS_BASE_DIR, "CityA")
    pt1_path_a = os.path.join(city_a_path, "PizzaLargePepperoni")
    pt1_inst1_path_a = os.path.join(pt1_path_a, "instance_001_older")
    pt1_inst2_path_a = os.path.join(pt1_path_a, "instance_002_newer")
    pt2_path_a = os.path.join(city_a_path, "SodaCan")
    pt2_inst1_path_a = os.path.join(pt2_path_a, "instanceA")

    os.makedirs(pt1_inst1_path_a, exist_ok=True)
    os.makedirs(pt1_inst2_path_a, exist_ok=True)
    os.makedirs(pt2_inst1_path_a, exist_ok=True)

    with open(os.path.join(pt1_inst1_path_a, "description.txt"), "w") as f: f.write("Oldest pepperoni pizza instance.")
    with open(os.path.join(pt1_inst1_path_a, "img1.jpg"), "w") as f: f.write("")
    with open(os.path.join(pt1_inst2_path_a, "description.txt"), "w") as f: f.write("Newer pepperoni pizza instance.")

    city_b_path = os.path.join(config.ITEMS_BASE_DIR, "CityB")
    pt3_path_b = os.path.join(city_b_path, "BurgerCombo")
    pt3_inst1_path_b = os.path.join(pt3_path_b, "combo_001")
    os.makedirs(pt3_inst1_path_b, exist_ok=True)
    # No description for pt3_inst1_path_b to test get_item_details returning None for product type

    print("--- Testing get_cities ---")
    cities = get_cities()
    print(f"Cities: {cities}")

    print("\n--- Testing get_items_in_city (Product Types) ---")
    items_in_city_a = get_items_in_city("CityA")
    print(f"Product Types in CityA: {items_in_city_a}")

    print("\n--- Testing get_product_instances ---")
    instances_pt1_a = get_product_instances(pt1_path_a)
    print(f"Instances for PizzaLargePepperoni in CityA: {instances_pt1_a}")

    print("\n--- Testing get_instance_details ---")
    details_inst1_pt1_a = get_instance_details(pt1_inst1_path_a)
    print(f"Details for {pt1_inst1_path_a}: {details_inst1_pt1_a}")
    details_inst_sodacan_a = get_instance_details(pt2_inst1_path_a) # No desc.txt
    print(f"Details for {pt2_inst1_path_a} (SodaCan instance, no desc.txt): {details_inst_sodacan_a}")

    print("\n--- Testing get_item_details (Product Type details from oldest instance) ---")
    details_pepperoni_a = get_item_details("CityA", "PizzaLargePepperoni")
    print(f"Details for Product Type 'PizzaLargePepperoni' in CityA: {details_pepperoni_a}")
    details_sodacan_a_type = get_item_details("CityA", "SodaCan") # SodaCan/instanceA has no desc
    print(f"Details for Product Type 'SodaCan' in CityA: {details_sodacan_a_type}")
    details_burger_b_type = get_item_details("CityB", "BurgerCombo") # BurgerCombo/combo_001 has no desc
    print(f"Details for Product Type 'BurgerCombo' in CityB: {details_burger_b_type}")


    print("\n--- Testing move_item_to_purchased ---")
    print(f"Attempting to move: CityA, PizzaLargePepperoni, instance_002_newer")
    move_success = move_item_to_purchased("CityA", "PizzaLargePepperoni", "instance_002_newer")
    print(f"Move successful: {move_success}")

    print("\nScript execution finished.")

def create_product_type_with_instance(city_name: str, product_type_name: str, instance_name: str, description: str, image_files: list) -> tuple[bool, str, str|None]:
    """
    Creates the folder structure for a new product type and its first instance.
    Writes the description.txt and saves images to the instance folder.

    Args:
        city_name: Name of the city.
        product_type_name: Name of the product type (e.g., "PizzaLargePepperoni").
        instance_name: Name of the initial instance (e.g., "instance_01").
        description: The item description text.
        image_files: A list of tuples, where each tuple is (file_bytes, filename_with_extension).

    Returns:
        A tuple (success_bool, message_str, product_folder_path_str_or_none).
    """
    product_folder_path = os.path.join(ITEMS_BASE_DIR, city_name, product_type_name)
    instance_folder_path = os.path.join(product_folder_path, instance_name)

    try:
        if os.path.exists(product_folder_path) and not os.path.isdir(product_folder_path):
            return False, f"Error: A file exists at the product path: {product_folder_path}", None
        if os.path.exists(instance_folder_path):
             return False, f"Error: Instance folder already exists: {instance_folder_path}", None

        os.makedirs(instance_folder_path, exist_ok=True)
        logger.info(f"Created directory: {instance_folder_path}")

        # Write description.txt
        desc_file_path = os.path.join(instance_folder_path, "description.txt")
        with open(desc_file_path, 'w', encoding='utf-8') as f:
            f.write(description)
        logger.info(f"Created description.txt in {instance_folder_path}")

        # Save images
        if image_files:
            saved_image_names = []
            for i, (img_bytes, img_filename) in enumerate(image_files):
                # Sanitize filename or use a fixed naming scheme if needed
                base, ext = os.path.splitext(img_filename)
                # A simple scheme: image1.ext, image2.ext
                # For admin uploads, maybe just use their filename if it's safe.
                # Here, let's use a generic name to avoid issues with complex user filenames.
                # safe_filename = f"image_{i+1}{ext}" # Example of renaming
                # For now, use original filename but log it. Be cautious with this in production.
                safe_filename = os.path.basename(img_filename) # Basic sanitization
                if not safe_filename: # handle if filename is weird like "../.."
                    safe_filename = f"image_{i+1}{ext}"

                img_path = os.path.join(instance_folder_path, safe_filename)
                try:
                    with open(img_path, 'wb') as img_f:
                        img_f.write(img_bytes)
                    saved_image_names.append(safe_filename)
                    logger.info(f"Saved image {safe_filename} to {instance_folder_path}")
                except IOError as e_img_save:
                    logger.error(f"Could not save image {img_filename} to {img_path}: {e_img_save}")
                    # Decide if this is a fatal error for the whole operation
                    # For now, continue and report partial success/failure based on description.

        return True, "Product type and first instance created successfully.", product_folder_path

    except OSError as e:
        logger.exception(f"OSError creating product/instance directories for {product_type_name} in {city_name}: {e}")
        return False, f"Filesystem error: {str(e)}", None
    except Exception as e_gen:
        logger.exception(f"General error in create_product_type_with_instance for {product_type_name}: {e_gen}")
        return False, f"An unexpected error occurred: {str(e_gen)}", None


def update_instance_description(instance_path: str, new_description: str) -> tuple[bool, str]:
    """Updates the description.txt of a specific instance."""
    if not os.path.isdir(instance_path):
        return False, "Instance folder not found."

    desc_file_path = os.path.join(instance_path, "description.txt")
    try:
        with open(desc_file_path, 'w', encoding='utf-8') as f:
            f.write(new_description)
        logger.info(f"Updated description for instance: {instance_path}")
        return True, "Description updated."
    except IOError as e:
        logger.exception(f"Failed to write description for instance {instance_path}: {e}")
        return False, "Error writing description file."

def add_image_to_instance(instance_path: str, image_bytes: bytes, image_filename: str) -> tuple[bool, str, str|None]:
    """Adds an image to a specific instance folder."""
    if not os.path.isdir(instance_path):
        return False, "Instance folder not found.", None

    # Basic filename sanitization
    base_filename = os.path.basename(image_filename)
    if not base_filename or base_filename in ['.', '..']: # rudimentary check
        return False, "Invalid image filename.", None

    img_full_path = os.path.join(instance_path, base_filename)

    if os.path.exists(img_full_path):
        # Handle overwrite or suggest renaming if needed. For now, overwrite.
        logger.warning(f"Image {base_filename} already exists in {instance_path}. Overwriting.")

    try:
        with open(img_full_path, 'wb') as f:
            f.write(image_bytes)
        logger.info(f"Added/updated image {base_filename} in instance: {instance_path}")
        return True, "Image added/updated successfully.", base_filename
    except IOError as e:
        logger.exception(f"Failed to save image {base_filename} to {instance_path}: {e}")
        return False, f"Error saving image: {str(e)}", None

def delete_file_from_instance(instance_path: str, filename_to_delete: str) -> tuple[bool, str]:
    """Deletes a specific file (e.g., an image) from an instance folder."""
    if not os.path.isdir(instance_path):
        return False, "Instance folder not found."

    file_path = os.path.join(instance_path, filename_to_delete)
    if os.path.isfile(file_path):
        try:
            os.remove(file_path)
            logger.info(f"Deleted file {filename_to_delete} from {instance_path}")
            return True, "File deleted successfully."
        except OSError as e:
            logger.exception(f"Error deleting file {file_path}: {e}")
            return False, f"Error deleting file: {str(e)}"
    else:
        return False, "File not found in instance."


def delete_item_folder_by_path(full_folder_path: str):
    """
    Deletes the specified folder and all its contents.
    """
    logger.info(f"Attempting to delete item folder: {full_folder_path}")
    if not full_folder_path or not isinstance(full_folder_path, str):
        logger.error(f"Invalid folder path provided for deletion: {full_folder_path}")
        return False, "Invalid folder path provided."

    if os.path.isdir(full_folder_path):
        try:
            shutil.rmtree(full_folder_path)
            logger.info(f"Successfully deleted folder: {full_folder_path}")
            return True, "Folder deleted successfully."
        except OSError as e:
            logger.exception(f"OSError deleting folder {full_folder_path}: {e}")
            return False, f"Error deleting folder: {str(e)}"
    else:
        logger.warning(f"Folder not found, cannot delete: {full_folder_path}")
        return False, "Folder not found."
