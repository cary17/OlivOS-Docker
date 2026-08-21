import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

GAI_IPV4_LINE = "RUN printf 'precedence ::ffff:0:0/96  100\\n' >> /etc/gai.conf"


class IPv4PreferenceTests(unittest.TestCase):
    """镜像必须内置 RFC 6724 IPv4 优先策略（gai.conf），避免阵发性 IPv6 黑洞拖慢出站请求。"""

    def test_main_dockerfile_builder_stage_sets_ipv4_preference(self):
        content = (ROOT / 'Dockerfile').read_text(encoding='utf-8')

        builder_part = content.split('FROM python:3.11-slim AS builder')[1].split('# ==================== 运行阶段 ====================')[0]
        runtime_part = content.split('# ==================== 运行阶段 ====================')[1]

        self.assertIn(GAI_IPV4_LINE, builder_part)
        self.assertIn(GAI_IPV4_LINE, runtime_part)

    def test_derived_dockerfile_sets_ipv4_preference(self):
        dockerfile_extra = ROOT / 'Dockerfile.extra'

        self.assertTrue(dockerfile_extra.exists())
        content = dockerfile_extra.read_text(encoding='utf-8')
        self.assertIn(GAI_IPV4_LINE, content)


if __name__ == '__main__':
    unittest.main()
