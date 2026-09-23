from svgeo.communes import department_from_commune, insee_code_from_stem
from svgeo.departements import code_from_url, norm_dep_name
from svgeo.utils import to_float, to_int


def test_commune_codes_from_file_names():
    assert insee_code_from_stem("022179") == "22179"
    assert insee_code_from_stem("02A004") == "2A004"
    assert insee_code_from_stem("58001") == "58001"
    assert insee_code_from_stem("971101") == "97101"
    assert insee_code_from_stem("ZA101") == "97101"
    assert insee_code_from_stem("001a") is None


def test_department_codes():
    assert department_from_commune("2A004") == "2A"
    assert department_from_commune("97101") == "971"
    assert code_from_url("https://x/presidentielle-2022/084/001/index.php") == "01"
    assert code_from_url("https://x/presidentielle-2022/094/02A/index.php") == "2A"
    assert norm_dep_name("Département de l'Ain (01)") == "ain"


def test_numbers():
    assert to_int("1 234 567") == 1234567
    assert to_int("1.234.567") == 1234567
    assert to_int("n/a") is None
    assert to_float("24,01 %") == 24.01
