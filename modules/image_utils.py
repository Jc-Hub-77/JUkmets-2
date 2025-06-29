import qrcode
import io

def generate_qr_code_image(data_string: str):
    """
    Generates a QR code image from the given data string.

    Args:
        data_string: The string to encode in the QR code.

    Returns:
        io.BytesIO: A file-like object containing the PNG image data,
                    or None if QR code generation fails.
    """
    if not data_string:
        return None

    try:
        qr = qrcode.QRCode(
            version=1, # Keep version low for simpler QR codes unless data is very long
            error_correction=qrcode.constants.ERROR_CORRECT_L, # Low error correction for smaller QR
            box_size=10, # Size of each box in pixels
            border=4,    # Border size in boxes
        )
        qr.add_data(data_string)
        qr.make(fit=True)

        img = qr.make_image(fill_color="black", back_color="white")

        img_byte_arr = io.BytesIO()
        img.save(img_byte_arr, format='PNG')
        img_byte_arr.seek(0) # Rewind the buffer to the beginning for reading

        return img_byte_arr
    except Exception as e:
        print(f"Error generating QR code for data '{data_string[:50]}...': {e}")
        return None

if __name__ == '__main__':
    # Example usage:
    test_data_simple = "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa" # Sample Bitcoin address
    test_data_uri = "bitcoin:1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa?amount=0.001"

    qr_image_bytes_simple = generate_qr_code_image(test_data_simple)
    if qr_image_bytes_simple:
        with open("test_qr_simple.png", "wb") as f:
            f.write(qr_image_bytes_simple.getvalue())
        print("Generated test_qr_simple.png")
    else:
        print("Failed to generate simple QR code.")

    qr_image_bytes_uri = generate_qr_code_image(test_data_uri)
    if qr_image_bytes_uri:
        with open("test_qr_uri.png", "wb") as f:
            f.write(qr_image_bytes_uri.getvalue())
        print("Generated test_qr_uri.png")
    else:
        print("Failed to generate URI QR code.")

    qr_image_bytes_empty = generate_qr_code_image("")
    if not qr_image_bytes_empty:
        print("Correctly handled empty string for QR data.")
    else:
        print("Error: QR code generated for empty string.")
