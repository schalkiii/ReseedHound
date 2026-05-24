import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.engine.seeder import ReseedStats  # noqa: E402
from src.notify.feishu import FeishuNotifier  # noqa: E402
from src.report.generator import ReportGenerator  # noqa: E402


def test_report_generator():
    stats = ReseedStats(
        total_torrents=37729,
        new_torrents=37729,
        matched_count=28456,
        succeeded_count=0,
        failed_count=0,
        site_details={
            "sewerpt": 6434,
            "novahd": 5041,
            "传道院": 3145,
            "njtupt": 2451,
            "13City": 2292,
            "lajidui": 1920,
            "昆仑": 1599,
            "ptzone": 1429,
            "hdbao": 1415,
            "三月传媒": 1311,
            "Sunny": 67,
            "憨憨": 0,
            "icc": 0,
            "青蛙": 0,
        },
    )

    generator = ReportGenerator()
    report = generator.generate(stats)
    print(report)

    assert "37729" in report
    assert "28456" in report
    assert "sewerpt" in report
    assert "6434" in report
    assert "启用站点:      14" in report
    assert "有匹配站点:    11" in report
    assert "无匹配站点:     3" in report

    md = generator.generate_markdown(stats)
    assert "37729" in md
    assert "| sewerpt | 6434 |" in md


def test_feishu_notifier_noop():
    notifier = FeishuNotifier.from_config({})
    assert notifier is None

    notifier2 = FeishuNotifier.from_config({"webhook_url": ""})
    assert notifier2 is None


def test_feishu_notifier_creation():
    notifier = FeishuNotifier.from_config({
        "webhook_url": "https://open.feishu.cn/test",
    })
    assert notifier is not None
    assert notifier._webhook_url == "https://open.feishu.cn/test"


if __name__ == "__main__":
    test_report_generator()
    test_feishu_notifier_noop()
    test_feishu_notifier_creation()
    print("\n全部测试通过!")
