import os
import shutil
import glob
import uuid
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

# Import config to use ITEMS_BASE_DIR
try:
    import config
    BASE_PRODUCT_DIR = getattr(config, 'ITEMS_BASE_DIR', "data/items")
    PURCHASED_ITEMS_DIR = getattr(config, 'PURCHASED_ITEMS_DIR', "data/purchased_items")
except ImportError:
    logger.error("config.py not found, using default paths for product directories.")
    BASE_PRODUCT_DIR = "data/items"
    PURCHASED_ITEMS_DIR = "data/purchased_items"


# Ensure base directories exist
os.makedirs(BASE_PRODUCT_DIR, exist_ok=True)
os.makedirs(PURCHASED_ITEMS_DIR, exist_ok=True)

def get_available_cities():
    """Scans the base product directory and returns a list of city names."""
    try:
        if not os.path.exists(BASE_PRODUCT_DIR) or not os.path.isdir(BASE_PRODUCT_DIR):
            logger.warning(f"Base product directory {BASE_PRODUCT_DIR} does not exist or is not a directory.")
            return []
        return [d for d in os.listdir(BASE_PRODUCT_DIR) if os.path.isdir(os.path.join(BASE_PRODUCT_DIR, d))]
    except Exception as e:
        logger.exception(f"Error listing cities from {BASE_PRODUCT_DIR}: {e}")
        return []

def get_available_areas(city_name):
    """Scans a city directory and returns a list of area names."""
    city_path = os.path.join(BASE_PRODUCT_DIR, city_name)
    try:
        return [d for d in os.listdir(city_path) if os.path.isdir(os.path.join(city_path, d))]
    except FileNotFoundError:
        logger.warning(f"City directory {city_path} not found.")
        return []
    except Exception as e:
        logger.exception(f"Error listing areas for city {city_name}: {e}")
        return []

def get_available_item_types(city_name, area_name):
    """Scans an area directory and returns a list of item type names."""
    area_path = os.path.join(BASE_PRODUCT_DIR, city_name, area_name)
    try:
        return [d for d in os.listdir(area_path) if os.path.isdir(os.path.join(area_path, d))]
    except FileNotFoundError:
        logger.warning(f"Area directory {area_path} not found.")
        return []
    except Exception as e:
        logger.exception(f"Error listing item types for {city_name}/{area_name}: {e}")
        return []

def get_available_sizes(city_name, area_name, item_type_name):
    """Scans an item type directory and returns a list of size names."""
    item_type_path = os.path.join(BASE_PRODUCT_DIR, city_name, area_name, item_type_name)
    try:
        return [d for d in os.listdir(item_type_path) if os.path.isdir(os.path.join(item_type_path, d))]
    except FileNotFoundError:
        logger.warning(f"Item type directory {item_type_path} not found.")
        return []
    except Exception as e:
        logger.exception(f"Error listing sizes for {city_name}/{area_name}/{item_type_name}: {e}")
        return []

def get_oldest_available_item_instance(city_name, area_name, item_type_name, size_name):
    """
    Scans a size directory for instance folders and returns the path to the oldest one.
    Oldest is determined by folder creation time.
    """
    size_path = os.path.join(BASE_PRODUCT_DIR, city_name, area_name, item_type_name, size_name)
    try:
        instance_folders = [os.path.join(size_path, d) for d in os.listdir(size_path)
                            if os.path.isdir(os.path.join(size_path, d))]
        if not instance_folders:
            return None

        # Sort by creation time (oldest first)
        # os.path.getctime might vary based on OS and filesystem nuances for directories.
        # A more robust way if strict ordering is needed is to embed timestamp in folder name.
        # For now, using ctime.
        instance_folders.sort(key=lambda x: os.path.getctime(x))
        return instance_folders[0]

    except FileNotFoundError:
        logger.warning(f"Size directory {size_path} not found.")
        return None
    except Exception as e:
        logger.exception(f"Error getting oldest instance for {size_path}: {e}")
        return None

def get_item_instance_details(instance_path):
    """
    Retrieves details (description, price, images) from an item instance folder.
    Assumes description.txt contains 'Price: XX.YY' on a line.
    Images are globbed based on common extensions.
    """
    details = {'description': '', 'price': 0.0, 'image_paths': []}
    desc_file_path = os.path.join(instance_path, "description.txt")

    try:
        # Read description and price
        if os.path.exists(desc_file_path):
            with open(desc_file_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()

            desc_lines = []
            for line in lines:
                if line.lower().startswith("price:"):
                    try:
                        details['price'] = float(line.split(":")[1].strip())
                    except (ValueError, IndexError) as e_price:
                        logger.error(f"Could not parse price from line: '{line.strip()}' in {desc_file_path}. Error: {e_price}")
                else:
                    desc_lines.append(line)
            details['description'] = "".join(desc_lines).strip()
        else:
            logger.warning(f"description.txt not found in {instance_path}")
            details['description'] = "Description not available."

        # Find images (jpg, png, gif, jpeg)
        image_patterns = ['*.jpg', '*.jpeg', '*.png', '*.gif']
        for pattern in image_patterns:
            details['image_paths'].extend(glob.glob(os.path.join(instance_path, pattern)))

        details['image_paths'] = sorted(details['image_paths'])[:3] # Max 3 images, sorted for consistency

        return details

    except Exception as e:
        logger.exception(f"Error getting item instance details from {instance_path}: {e}")
        return { # Return default/error structure
            'description': 'Error loading details.',
            'price': 0.0,
            'image_paths': []
        }

def move_item_instance_to_purchased(instance_path, user_id):
    """Moves an item instance folder to the purchased items directory for the user."""
    if not os.path.isdir(instance_path):
        logger.error(f"Instance path {instance_path} does not exist or is not a directory.")
        return False

    try:
        user_purchase_dir = os.path.join(PURCHASED_ITEMS_DIR, str(user_id))
        os.makedirs(user_purchase_dir, exist_ok=True)

        original_instance_name = os.path.basename(instance_path)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        new_instance_name = f"{original_instance_name}_{timestamp}"
        destination_path = os.path.join(user_purchase_dir, new_instance_name)

        shutil.move(instance_path, destination_path)
        logger.info(f"Moved {instance_path} to {destination_path}")
        return True
    except Exception as e:
        logger.exception(f"Error moving instance {instance_path} to purchased for user {user_id}: {e}")
        return False

def add_item_instance(city, area, item_type, size, price: float, images: list[tuple[str, bytes]], description: str):
    """
    Adds a new item instance to the filesystem.
    Images is a list of (original_filename, file_bytes).
    """
    instance_folder_name = str(uuid.uuid4()) # Unique folder name for the instance
    instance_path = os.path.join(BASE_PRODUCT_DIR, city, area, item_type, size, instance_folder_name)

    try:
        os.makedirs(instance_path, exist_ok=True)

        # Save description.txt
        desc_file_path = os.path.join(instance_path, "description.txt")
        with open(desc_file_path, 'w', encoding='utf-8') as f:
            f.write(f"Price: {price:.2f}\n")
            f.write(description)

        # Save images
        for i, (original_filename, file_bytes) in enumerate(images[:3]): # Max 3 images
            _, ext = os.path.splitext(original_filename)
            if not ext: # Ensure there's an extension
                ext = '.jpg' # Default to jpg if no extension found
            image_filename = f"image{i+1}{ext}" # e.g., image1.jpg
            image_path = os.path.join(instance_path, image_filename)
            with open(image_path, 'wb') as f:
                f.write(file_bytes)

        logger.info(f"Successfully added new item instance to {instance_path}")
        return instance_path

    except Exception as e:
        logger.exception(f"Error adding new item instance to {city}/{area}/{item_type}/{size}: {e}")
        # Attempt to clean up partially created directory if error occurs
        if os.path.exists(instance_path):
            try:
                shutil.rmtree(instance_path)
                logger.info(f"Cleaned up partially created directory: {instance_path}")
            except Exception as e_cleanup:
                logger.error(f"Error cleaning up directory {instance_path}: {e_cleanup}")
        return None

if __name__ == '__main__':
    # Basic test logic
    logger.setLevel(logging.INFO)
    logging.basicConfig(level=logging.INFO)

    # Create some dummy structure for testing
    test_city = "TestCity"
    test_area = "TestArea"
    test_type = "TestType"
    test_size = "TestSize"

    # Test add_item_instance
    logger.info("Testing add_item_instance...")
    dummy_image_bytes = b"dummyimagedata"
    test_images = [("test1.jpg", dummy_image_bytes), ("test2.png", dummy_image_bytes)]
    test_desc = "This is a test item description."

    # Clean up before test
    test_base_path = os.path.join(BASE_PRODUCT_DIR, test_city)
    if os.path.exists(test_base_path):
        shutil.rmtree(test_base_path)

    added_path = add_item_instance(test_city, test_area, test_type, test_size, 10.99, test_images, test_desc)
    if added_path:
        logger.info(f"Item added to: {added_path}")
    else:
        logger.error("Failed to add item.")

    added_path2 = add_item_instance(test_city, test_area, test_type, test_size, 12.50, [("img.jpeg", dummy_image_bytes)], "Another item.")
    if added_path2:
        logger.info(f"Second item added to: {added_path2}")
        # Make this one seem older for testing get_oldest_available_item_instance
        # This is tricky as ctime is hard to modify directly.
        # For real testing, folder names with timestamps would be better.
        # We'll rely on the order of creation for this simple test.
        if os.path.exists(added_path) and os.path.exists(added_path2) and added_path != added_path2:
             # Ensure added_path is older by touching it with an older timestamp (platform dependent)
            pass


    # Test listing functions
    logger.info(f"Cities: {get_available_cities()}")
    logger.info(f"Areas in {test_city}: {get_available_areas(test_city)}")
    logger.info(f"Types in {test_city}/{test_area}: {get_available_item_types(test_city, test_area)}")
    logger.info(f"Sizes in {test_city}/{test_area}/{test_type}: {get_available_sizes(test_city, test_area, test_type)}")

    # Test get_oldest_available_item_instance
    oldest_instance = get_oldest_available_item_instance(test_city, test_area, test_type, test_size)
    if oldest_instance:
        logger.info(f"Oldest instance: {oldest_instance}")
        if oldest_instance != added_path: # Assuming added_path was created first
            logger.warning(f"Oldest instance logic might need review; expected {added_path}, got {oldest_instance}")

        # Test get_item_instance_details
        details = get_item_instance_details(oldest_instance)
        logger.info(f"Details for {oldest_instance}: {details}")
        if details['price'] != 10.99:
             logger.error(f"Price mismatch: expected 10.99, got {details['price']}")
        if len(details['image_paths']) != 2:
             logger.error(f"Image count mismatch: expected 2, got {len(details['image_paths'])}")


        # Test move_item_instance_to_purchased
        test_user_id = "test_user_123"
        if move_item_instance_to_purchased(oldest_instance, test_user_id):
            logger.info(f"Successfully moved {oldest_instance} for user {test_user_id}")
            if os.path.exists(oldest_instance):
                logger.error(f"Source instance {oldest_instance} still exists after move!")
        else:
            logger.error(f"Failed to move {oldest_instance}")

    else:
        logger.error("Could not retrieve oldest instance for testing details and move.")

    # Clean up test data
    if os.path.exists(test_base_path):
        shutil.rmtree(test_base_path)
    test_user_purchase_path = os.path.join(PURCHASED_ITEMS_DIR, "test_user_123")
    if os.path.exists(test_user_purchase_path):
        shutil.rmtree(test_user_purchase_path)
    logger.info("Basic tests finished.")
