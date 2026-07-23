import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class DerivedImageConfigurationTests(unittest.TestCase):
    def test_runtime_entrypoint_does_not_install_extra_packages(self):
        entrypoint = (ROOT / 'entrypoint.sh').read_text(encoding='utf-8')

        self.assertNotIn('EXTRA_PACKAGES', entrypoint)
        self.assertNotIn('pip install', entrypoint)

    def test_derived_dockerfile_installs_requirements_at_build_time(self):
        dockerfile = ROOT / 'Dockerfile.extra'

        self.assertTrue(dockerfile.exists())
        content = dockerfile.read_text(encoding='utf-8')
        self.assertIn('ARG BASE_IMAGE', content)
        self.assertIn('FROM ${BASE_IMAGE}', content)
        self.assertIn('COPY requirements-extra.txt', content)
        self.assertIn('COPY entrypoint.sh /entrypoint.sh', content)
        self.assertIn('ENTRYPOINT ["/entrypoint.sh"]', content)
        self.assertIn('pip install', content)

    def test_extra_compose_uses_build_only_configuration(self):
        compose = ROOT / 'docker-compose.extra.yml'

        self.assertTrue(compose.exists())
        content = compose.read_text(encoding='utf-8')
        self.assertIn('dockerfile: Dockerfile.extra', content)
        self.assertIn('BASE_IMAGE:', content)
        self.assertNotIn('EXTRA_PACKAGES', content)

    def test_ci_builds_the_derived_image(self):
        workflow = (ROOT / '.github/workflows/build.yml').read_text(encoding='utf-8')

        self.assertIn('docker build', workflow)
        self.assertIn('Dockerfile.extra', workflow)

    def test_readme_documents_extra_packages_migration(self):
        readme = (ROOT / 'README.md').read_text(encoding='utf-8')

        self.assertIn('EXTRA_PACKAGES', readme)
        self.assertIn('requirements-extra.txt', readme)
        self.assertIn('### 方法一：', readme)
        self.assertIn('### 方法二：', readme)
        self.assertIn('### 方法三：', readme)


if __name__ == '__main__':
    unittest.main()
