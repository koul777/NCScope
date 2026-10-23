"""Official skill rows retain their action across all seven interview methods."""

from __future__ import annotations

import copy
import re

import pytest

from app.main import SUPPORTED_INTERVIEW_METHODS, _attach_ksa_evidence_to_strategy
from app.services.question_surface import public_task_object, stable_ksa_evidence_id
from scripts.benchmark_question_method_matrix import (
    build_template_question,
    evidence_row,
)


# Snapshots from the read-only NCS MCP database, using the same official fields
# and joins as the method matrix. Tests run offline and do not need that DB.
OFFICIAL_SKILL_CASES = (
    (
        "수집",
        {
            "major_code": "01",
            "major_name": "사업관리",
            "sub_name": "프로젝트관리",
            "unit_code": "0101010202_17v2",
            "unit_name": "프로젝트 통합관리",
            "unit_definition": "프로젝트 통합관리는 다양한 프로젝트관리 활동들을 식별, 정의, 통합 및 조정하여 이해관계자의 요구 사항과 기대치를 성공적으로 관리하고 상호 영향 관계를 "
            "고려하여 프로젝트 착수부터 종료까지 관리하는 능력이다.",
            "element_name": "프로젝트관리계획서 개발하기",
            "ksa_type": "기술",
            "factor_name": "의사결정 사안을 수집할 수 있는 능력",
            "ksa_no": "2",
        },
        "의사결정 사안 수집·확인 절차",
        "의사결정 사안 수집",
    ),
    (
        "정보검색",
        {
            "major_code": "17",
            "major_name": "화학·바이오",
            "sub_name": "의약품연구개발",
            "unit_code": "1703060308_21v1",
            "unit_name": "의약품 인허가",
            "unit_definition": "의약품 인허가란 규제기관으로부터 의약품제조 및 유통에 대한 인허가를 받기 위해 의약품 인허가 규정파악, 해외 의약품규격 · 기준 파악 및 CTD 문서작성 "
            "등을 수행하는 능력이다.",
            "element_name": "의약품 인허가 규정 파악하기",
            "ksa_type": "기술",
            "factor_name": "의약품 인허가와 관련된 정보검색 기술",
            "ksa_no": "1",
        },
        "의약품 인허가와 관련된 정보 검색·확인 절차",
        "의약품 인허가와 관련된 정보 검색",
    ),
    (
        "분류",
        {
            "major_code": "02",
            "major_name": "경영·회계·사무",
            "sub_name": "회계·감사",
            "unit_code": "0203020103_20v4",
            "unit_name": "원가계산",
            "unit_definition": "원가계산이란 기업운영에 있어 원가분석 및 정보를 제공·활용하기 위해 원가요소 분류, 배부, 계산하는 능력이다.",
            "element_name": "원가요소 분류하기",
            "ksa_type": "기술",
            "factor_name": "원가의 다양한 관점에 따른 분류 능력",
            "ksa_no": "3",
        },
        "원가의 다양한 관점에 따른 분류·확인 절차",
        "원가의 다양한 관점에 따른 분류",
    ),
    (
        "처리",
        {
            "major_code": "01",
            "major_name": "사업관리",
            "sub_name": "해외취업관리",
            "unit_code": "0101020207_15v1",
            "unit_name": "해외취업 알선",
            "unit_definition": "해외취업 알선이란 해외취업관련정보를 토대로 알선계획을 수립하여 해외취업을 원하는 구직자를 해외구인처에 소개하고 해외구인처가 필요한 인재를 채용할 수 "
            "있도록 과정을 관리하고 지원할 수 있는 능력이다.",
            "element_name": "해외취업 구직자 채용 확인하기",
            "ksa_type": "기술",
            "factor_name": "채용여부 확인 관련 행정서류처리 능력",
            "ksa_no": "2",
        },
        "채용여부 확인 관련 행정서류 처리·확인 절차",
        "채용여부 확인 관련 행정서류 처리",
    ),
    (
        "운영",
        {
            "major_code": "01",
            "major_name": "사업관리",
            "sub_name": "프로젝트관리",
            "unit_code": "0101010211_15v1",
            "unit_name": "프로젝트 의사소통관리",
            "unit_definition": "프로젝트 의사소통관리란 프로젝트 이해관계자들에게 적시에 필요한 정보와 의사소통 요구사항을 제공하기 위해 의사소통 방안을 계획 수립하고, 의사소통 계획에 "
            "따라 필요한 정보를 수집 배포하며, 배포된 정보가 이해관계자의 의사소통 요구사항을 만족하는지 모니터링 및 조정하는 능력이다.",
            "element_name": "정보 배포하기",
            "ksa_type": "기술",
            "factor_name": "정보관리시스템을 운영할 수 있는 능력",
            "ksa_no": "5",
        },
        "정보관리시스템 운영·점검 절차",
        "정보관리시스템 운영",
    ),
    (
        "관리",
        {
            "major_code": "02",
            "major_name": "경영·회계·사무",
            "sub_name": "총무",
            "unit_code": "0202010107_25v3",
            "unit_name": "업무지원",
            "unit_definition": "업무지원이란 조직 구성원들이 업무상 발생되는 인장, 출장, 제 증명 관련 요청 사항을 적시에 지원하고 업무용 시설을 관리, 개선할 수 있는 능력이다.",
            "element_name": "제 증명 관리하기",
            "ksa_type": "기술",
            "factor_name": "법인 제증명 유효기간과 재고관리 능력",
            "ksa_no": "1",
        },
        "법인 제증명 유효기간과 재고 관리·점검 절차",
        "법인 제증명 유효기간과 재고 관리",
    ),
    (
        "파악",
        {
            "major_code": "01",
            "major_name": "사업관리",
            "sub_name": "프로젝트관리",
            "unit_code": "0101010202_17v2",
            "unit_name": "프로젝트 통합관리",
            "unit_definition": "프로젝트 통합관리는 다양한 프로젝트관리 활동들을 식별, 정의, 통합 및 조정하여 이해관계자의 요구 사항과 기대치를 성공적으로 관리하고 상호 영향 관계를 "
            "고려하여 프로젝트 착수부터 종료까지 관리하는 능력이다.",
            "element_name": "프로젝트헌장 개발하기",
            "ksa_type": "기술",
            "factor_name": "계약서의 내용을 파악할 수 있는 능력",
            "ksa_no": "1",
        },
        "계약서의 내용 확인 절차",
        "계약서의 내용 파악",
    ),
    (
        "점검",
        {
            "major_code": "03",
            "major_name": "금융·보험",
            "sub_name": "기업영업",
            "unit_code": "0301010208_14v1",
            "unit_name": "거래약정",
            "unit_definition": "거래약정이란, 승인조건에 부합되는 제반약정사항을 점검하고, 거래 실행 후 이행사항을 점검하는 능력이다.",
            "element_name": "거래약정 감리하기",
            "ksa_type": "기술",
            "factor_name": "승인조건에 따른 이행 여부 점검 능력",
            "ksa_no": "2",
        },
        "승인조건에 따른 이행 여부 확인 절차",
        "승인조건에 따른 이행 여부 점검",
    ),
    (
        "설정",
        {
            "major_code": "14",
            "major_name": "건설",
            "sub_name": "건설공사환경관리",
            "unit_code": "1401020309_25v3",
            "unit_name": "환경오염물질 관리",
            "unit_definition": "환경오염물질 관리란 환경오염원을 파악하여 발생량을 예측하고, 대상 오염물질 법적기준에 따라 관리대상을 선정하며, 해당 환경오염 공정시험방법에 의거하여 "
            "측정 및 분석·처리하는 능력이다.",
            "element_name": "대상오염물 관리기준 설정하기",
            "ksa_type": "기술",
            "factor_name": "오염원 및 공정별 관리기준 설정 능력",
            "ksa_no": "2",
        },
        "오염원 및 공정별 관리기준 설정·확인 절차",
        "오염원 및 공정별 관리기준 설정",
    ),
    (
        "선택",
        {
            "major_code": "08",
            "major_name": "문화·예술·디자인·방송",
            "sub_name": "영상편집",
            "unit_code": "0803040606_15v2",
            "unit_name": "효과편집",
            "unit_definition": "효과편집이란 영상물의 내용에 따라 특수영상을 제작하거나 적절한 비디오필터 및 화면전환효과를 적용하는 능력이다.",
            "element_name": "비선형효과 편집하기",
            "ksa_type": "기술",
            "factor_name": "화면전환효과 및 비디오효과 선택 능력",
            "ksa_no": "1",
        },
        "화면전환효과 및 비디오효과 선정·확인 절차",
        "화면전환효과 및 비디오효과 선택",
    ),
    (
        "선정",
        {
            "major_code": "02",
            "major_name": "경영·회계·사무",
            "sub_name": "통계조사",
            "unit_code": "0201030316_18v1",
            "unit_name": "비즈니스 인텔리전스 지원",
            "unit_definition": "비즈니스 인텔리전스 지원이란 정보통신기술을 통해 시스템에 축적된 내‧외부의 데이터를 수집, 가공, 분석하여 경영 관련 의사결정을 위해 필요한 정보를 "
            "제공하는 능력이다.",
            "element_name": "데이터 분석방법 선정하기",
            "ksa_type": "기술",
            "factor_name": "분석목표에 부합하는 데이터 선정 능력",
            "ksa_no": "3",
        },
        "분석목표에 부합하는 데이터 선정·확인 절차",
        "분석목표에 부합하는 데이터 선정",
    ),
    (
        "선별",
        {
            "major_code": "08",
            "major_name": "문화·예술·디자인·방송",
            "sub_name": "캐릭터콘텐츠유통·서비스",
            "unit_code": "0803030809_15v1",
            "unit_name": "캐릭터 상품 유통 마케팅(라이선시)",
            "unit_definition": "캐릭터 상품 유통 마케팅(라이선시)이란 상품판매 촉진을 위하여 관련 매체를 통해 광고, 홍보하는 능력이다.",
            "element_name": "캐릭터 상품 광고매체 선정하기",
            "ksa_type": "기술",
            "factor_name": "효과적인 매체를 선별할 수 있는 능력",
            "ksa_no": "3",
        },
        "효과적인 매체 선정·확인 절차",
        "효과적인 매체 선별",
    ),
    (
        "조사",
        {
            "major_code": "06",
            "major_name": "보건·의료",
            "sub_name": "병원행정",
            "unit_code": "0601020104_13v1",
            "unit_name": "병원구매관리",
            "unit_definition": "병원구매관리란 병원운영과 진료에 필요한 물자를 구입하고 관리할 수 있는 능력이다.",
            "element_name": "약품 구매하기",
            "ksa_type": "기술",
            "factor_name": "종전 구입가격 및 시장가격 조사 능력",
            "ksa_no": "2",
        },
        "종전 구입가격 및 시장가격 조사·확인 절차",
        "종전 구입가격 및 시장가격 조사",
    ),
    (
        "배양",
        {
            "major_code": "14",
            "major_name": "건설",
            "sub_name": "해양생태환경조사",
            "unit_code": "1408010308_14v1",
            "unit_name": "해양미생물조사",
            "unit_definition": "해양미생물조사란 해양환경에 서식하는 미생물을 분리·배양하거나 시료에서 직접 DNA 및 RNA를 분리하여 그 군집의 분포와 기능을 현미경적·분자생물학적 "
            "기법으로 분석·확인하고 획득된 정보를 학술적·산업적으로 활용하는 능력이다.",
            "element_name": "미생물군집 구조·기능 분석하기",
            "ksa_type": "기술",
            "factor_name": "유용해양미생물의 선별·대량 배양 능력",
            "ksa_no": "3",
        },
        "유용해양미생물의 선별·대량 배양·확인 절차",
        "유용해양미생물의 선별·대량 배양",
    ),
    (
        "수행",
        {
            "major_code": "04",
            "major_name": "교육·자연·사회과학",
            "sub_name": "이러닝시스템개발",
            "unit_code": "0403020109_16v3",
            "unit_name": "학습시스템 이전",
            "unit_definition": "학습 시스템 이전이란 개발된 학습 시스템을인수자가 사용할 수 있도록 교육계획을 수립하여 교육하고 교육목표가 달성되었는지 파악하는 능력이다.",
            "element_name": "학습시스템 사용자 교육하기",
            "ksa_type": "기술",
            "factor_name": "피교육생의 수준에 맞는 교육 수행능력",
            "ksa_no": "2",
        },
        "피교육생의 수준에 맞는 교육 수행·검증 절차",
        "피교육생의 수준에 맞는 교육",
    ),
    (
        "유지",
        {
            "major_code": "09",
            "major_name": "운전·운송",
            "sub_name": "소형무인기운용·조종",
            "unit_code": "0904010513_16v1",
            "unit_name": "소형무인기 이송 운용",
            "unit_definition": "소형무인기 이송 운용이란 소형무인기를 이용하여 응급, 재난, 긴급상황 발생 시 또는 일반물품을 목적지까지 원활하게 이동시키기 위하여 이송계획 확인, "
            "이송장비 점검, 이송물품을 적재, 운반, 하역하는 능력이다.",
            "element_name": "이송물품 적재하기",
            "ksa_type": "기술",
            "factor_name": "비행중량 체크 및 무게중심 유지 능력",
            "ksa_no": "4",
        },
        "비행중량 체크 및 무게중심 유지·점검 절차",
        "비행중량 체크 및 무게중심 유지",
    ),
    (
        "파악-목적격",
        {
            "major_code": "02",
            "major_name": "경영·회계·사무",
            "sub_name": "공정관리",
            "unit_code": "0204010314_23v1",
            "unit_name": "설비효율관리",
            "unit_definition": "설비효율관리란 산출 분석을 통하여 설비효율을 저하시키는 로스(LOSS)를 유형별로 분류하여 지속적으로 개선함으로써 설비효율을 극대화할 수 있도록 관리하는 "
            "능력이다.",
            "element_name": "설비효율관리 항목 분석하기",
            "ksa_type": "기술",
            "factor_name": "설비 LOSS 항목을 파악 능력",
            "ksa_no": "6",
        },
        "설비 LOSS 항목을 확인 절차",
        "설비 LOSS 항목 파악",
    ),
    (
        "수집-분리어미",
        {
            "major_code": "08",
            "major_name": "문화·예술·디자인·방송",
            "sub_name": "방송콘텐츠유통·서비스",
            "unit_code": "0803030108_15v1",
            "unit_name": "방송콘텐츠 데이터 관리",
            "unit_definition": "방송콘텐츠 데이터관리란 방송콘텐츠의 소재와 원본을 관리하는 능력이다.",
            "element_name": "방송콘텐츠 데이터 관리하기",
            "ksa_type": "기술",
            "factor_name": "메타 데이터를 수집 하는 능력",
            "ksa_no": "1",
        },
        "메타 데이터를 수집·확인 절차",
        "메타 데이터 수집",
    ),
    (
        "유지-목적격",
        {
            "major_code": "12",
            "major_name": "이용·숙박·여행·오락·스포츠",
            "sub_name": "부대시설관리",
            "unit_code": "1203020310_24v3",
            "unit_name": "휘트니스 센터 운영",
            "unit_definition": "휘트니스 센터 운영이란 이용객의 편의를 제공하기 위해 체련장, 수영장, 골프연습장, 스파·사우나 등의 레저 스포츠 시설을 운영·관리하는 능력이다.",
            "element_name": "스파· 사우나 운영하기",
            "ksa_type": "기술",
            "factor_name": "스파·사우나 운영시스템에 따라 수질을 유지 능력",
            "ksa_no": "2",
        },
        "스파·사우나 운영시스템에 따라 수질을 유지·점검 절차",
        "스파·사우나 운영시스템에 따라 수질 유지",
    ),
    (
        "관리-목적격",
        {
            "major_code": "12",
            "major_name": "이용·숙박·여행·오락·스포츠",
            "sub_name": "스포츠용품제작",
            "unit_code": "1204010108_20v2",
            "unit_name": "스포츠용품 생산관리",
            "unit_definition": "스포츠용품 생산관리란 불량률 최소화 및 원가절감을 위해 적절한 인원배치를 기반으로 제품생산이 원활하게 이루어질 수 있도록 관리하는 능력이다.",
            "element_name": "생산설비 유지보수 관리하기",
            "ksa_type": "기술",
            "factor_name": "데이터를 관리 능력",
            "ksa_no": "3",
        },
        "데이터를 관리·점검 절차",
        "데이터 관리",
    ),
    (
        "배양-의도어미",
        {
            "major_code": "12",
            "major_name": "이용·숙박·여행·오락·스포츠",
            "sub_name": "레크리에이션지도",
            "unit_code": "1204050108_16v2",
            "unit_name": "연기지도",
            "unit_definition": "연기지도란 발음 발성과 신체훈련, 화술훈련으로 이미지를 창조하여 장면 연기 및 즉흥 연기 훈련을 지도하는 능력이다.",
            "element_name": "연기 훈련하기",
            "ksa_type": "기술",
            "factor_name": "자연스러움을 배양하려는 능력",
            "ksa_no": "2",
        },
        "자연스러움을 배양·확인 절차",
        "자연스러움 배양",
    ),
    (
        "처리-목적격",
        {
            "major_code": "15",
            "major_name": "기계",
            "sub_name": "선박도장",
            "unit_code": "1508020314_23v4",
            "unit_name": "선박도장 기능성 특수도장",
            "unit_definition": "선박도장 기능성 특수도장이란, 선박용 강재와 블록, 선체, 플랜트 구조물 등을 대상으로 방청, 방오 및 미관 개선의 목적 외 특수한 기능을 부여하기 위해 "
            "도장시방서에 따라 도료를 이용하여 스프레이 및 터치업 방식으로 도장하는 능력이다.",
            "element_name": "선박도장 기능성 특수도장 마무리하기",
            "ksa_type": "기술",
            "factor_name": "잔량의 도료 및 폐도료를 처리 능력",
            "ksa_no": "4",
        },
        "잔량의 도료 및 폐도료를 처리·확인 절차",
        "잔량의 도료 및 폐도료 처리",
    ),
    (
        "분류-목적격",
        {
            "major_code": "19",
            "major_name": "전기·전자",
            "sub_name": "전자응용기기기구개발",
            "unit_code": "1903040204_22v2",
            "unit_name": "기구 설계",
            "unit_definition": "기구 설계란 부품의 구조설계와 시뮬레이션 분석을 통해 상세설계하기 위한 능력이다.",
            "element_name": "구조 설계하기",
            "ksa_type": "기술",
            "factor_name": "기능·성능 정보를 분류 기술",
            "ksa_no": "1",
        },
        "기능·성능 정보를 분류·확인 절차",
        "기능·성능 정보 분류",
    ),
    (
        "관리-중복",
        {
            "major_code": "19",
            "major_name": "전기·전자",
            "sub_name": "전기공사관리",
            "unit_code": "1901070403_22v1",
            "unit_name": "전기공사 공정관리",
            "unit_definition": "전기공사 공정관리란 계약된 공사기간내에 공사단계별 필요한 모든 작업에 대한 순서와 일정, 자재, 장비, 인원, 공사비, 공법 등의 적절한 투입과 공정을 "
            "계획하고 공사진척의 관리와 점검, 공정간 협의와 조정 등 공사의 총체적인 과정을 수행하는 능력이다.",
            "element_name": "자재투입 관리하기",
            "ksa_type": "기술",
            "factor_name": "기자재수급관리 관리능력",
            "ksa_no": "6",
        },
        "기자재수급관리 관리·점검 절차",
        "기자재수급 관리",
    ),
)

_STACKED_ACTION_RE = re.compile(
    r"(?:수집|정보\s*검색|분류|처리|운영|관리|파악|점검|설정|선택|선정|선별|조사|배양|수행|유지)"
    r"\s*['\"’]?\s*(?:을|를)\s*(?:직접\s*|실제로\s*)?수행"
)


@pytest.mark.parametrize(
    ("factor", "element", "expected"),
    (
        ("면담 수행 능력", "경력코칭 모니터링하기", "면담 수행"),
        ("세척 수행 능력", "오염물품 세척하기", "세척 수행"),
        ("검사수행 능력", "도면 확인하기", "검사 수행"),
    ),
)
def test_short_performance_task_keeps_factor_anchor(
    factor: str,
    element: str,
    expected: str,
) -> None:
    # Official rows: 0403010115_22v3, 0601010806_23v3, 1507010213_19v3.
    # A two-character object is rejected by the existing public-object guard.
    # Retain 수행 instead of falling back to another task or inventing 검증.
    surface, source = public_task_object(
        factor_name=factor,
        ksa_type="기술",
        element_name=element,
    )

    assert surface == expected
    assert source == "factor_repair"
    assert "모니터링" not in surface
    assert "도면 확인" not in surface


@pytest.mark.parametrize(
    ("rule", "row", "old_surface", "expected"),
    OFFICIAL_SKILL_CASES,
    ids=[case[0] for case in OFFICIAL_SKILL_CASES],
)
def test_remaining_skill_surface_preserves_original_action(
    rule: str,
    row: dict[str, str],
    old_surface: str,
    expected: str,
) -> None:
    surface, source = public_task_object(
        factor_name=row["factor_name"],
        ksa_type=row["ksa_type"],
        element_name=row["element_name"],
        competency_name=row["unit_name"],
    )

    assert surface == expected, rule
    assert source == "factor_repair"
    assert old_surface not in surface
    assert "절차" not in surface
    for invented_action in ("확인", "점검", "검증", "선정"):
        if invented_action not in row["factor_name"]:
            assert invented_action not in surface


@pytest.mark.parametrize("method", SUPPORTED_INTERVIEW_METHODS)
@pytest.mark.parametrize(
    ("rule", "row", "old_surface", "expected"),
    OFFICIAL_SKILL_CASES,
    ids=[case[0] for case in OFFICIAL_SKILL_CASES],
)
def test_remaining_skill_every_method_keeps_action_and_evidence(
    method: str,
    rule: str,
    row: dict[str, str],
    old_surface: str,
    expected: str,
) -> None:
    for variation_index in (0, 27):
        evidence = evidence_row(row)
        item = build_template_question(row, method, variation_index=variation_index)
        evaluated = _attach_ksa_evidence_to_strategy(
            {
                "interview_questions": [item],
                "question_plan_used": {"total_main_count": 1},
            },
            [evidence],
        )
        question = evaluated["interview_questions"][0]
        quality = evaluated["question_quality_report"]["items"][0]
        visible = "\n".join([question["question"], *question["follow_ups"]])

        assert question["question_focus_surface"] == expected, rule
        assert expected in question["question"]
        assert old_surface not in visible
        assert f"{expected}에 따라" not in visible
        assert f"{expected} 절차" not in visible
        assert not _STACKED_ACTION_RE.search(visible), visible
        assert "관리 관리" not in visible
        assert question["question_focus"] == row["factor_name"]
        assert question["ksa_evidence"][0]["factorName"] == row["factor_name"]
        assert question["question_evidence_id"] == stable_ksa_evidence_id(evidence)
        assert quality["ready"] is True, (method, rule, quality.get("issues"))
        assert quality["score"] == 1.0

        second_pass = _attach_ksa_evidence_to_strategy(
            {
                "interview_questions": [copy.deepcopy(question)],
                "question_plan_used": {"total_main_count": 1},
            },
            [evidence],
        )["interview_questions"][0]
        for key in (
            "question",
            "follow_ups",
            "question_focus_surface",
            "question_evidence_id",
        ):
            assert second_pass[key] == question[key], (method, rule, key)
