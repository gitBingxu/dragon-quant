"""SigV4 单元测试：AWS 官方向量 + 头部覆盖。"""

import hashlib
import unittest

from dragon_quant.infra.assets.sync.backends import signer

ACCESS_KEY = "AKIAIOSFODNN7EXAMPLE"
SECRET_KEY = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"


class TestAwsVector(unittest.TestCase):
    """AWS 文档 Example: GET Object。"""

    def test_get_object_signature(self):
        headers = signer.sign_request(
            "GET",
            "https://examplebucket.s3.amazonaws.com/test.txt",
            {"range": "bytes=0-9"},
            b"",
            access_key=ACCESS_KEY,
            secret_key=SECRET_KEY,
            region="us-east-1",
            amz_date="20130524T000000Z",
        )
        self.assertEqual(
            headers["authorization"],
            "AWS4-HMAC-SHA256 "
            "Credential=AKIAIOSFODNN7EXAMPLE/20130524/us-east-1/s3/aws4_request, "
            "SignedHeaders=host;range;x-amz-content-sha256;x-amz-date, "
            "Signature=f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41",
        )
        self.assertEqual(headers["x-amz-content-sha256"], signer.EMPTY_SHA256)

    def test_put_object_signature_with_encoded_path(self):
        """AWS 文档 Example: PUT Object（含 %24 编码路径与 body）。"""
        body = b"Welcome to Amazon S3."
        headers = signer.sign_request(
            "PUT",
            "https://examplebucket.s3.amazonaws.com/test%24file.text",
            {
                "date": "Fri, 24 May 2013 00:00:00 GMT",
                "x-amz-storage-class": "REDUCED_REDUNDANCY",
            },
            body,
            access_key=ACCESS_KEY,
            secret_key=SECRET_KEY,
            region="us-east-1",
            amz_date="20130524T000000Z",
        )
        self.assertEqual(
            headers["x-amz-content-sha256"],
            "44ce7dd67c959e0d3524ffac1771dfbba87d2b6b4b4e99e42034a8b803f8b072",
        )
        self.assertIn(
            "Signature=98ad721746da40c64f1a55b78f14c238d841ea1380cd77a1b5971af0ece108bd",
            headers["authorization"],
        )


class TestSignedHeaders(unittest.TestCase):
    def test_body_hash_and_metadata_signed(self):
        body = b'{"a":1}'
        headers = signer.sign_request(
            "PUT",
            "https://s3.us-west-004.backblazeb2.com/bucket/blobs/2026-10-19/prep_dragons.abc.json.gz",
            {"content-md5": "abc", "x-amz-meta-sha256": "def"},
            body,
            access_key=ACCESS_KEY,
            secret_key=SECRET_KEY,
            region="us-west-004",
            amz_date="20261019T120000Z",
        )
        self.assertEqual(headers["x-amz-content-sha256"], hashlib.sha256(body).hexdigest())
        self.assertIn("content-md5", headers["authorization"])
        self.assertIn("x-amz-meta-sha256", headers["authorization"])
        self.assertTrue(headers["authorization"].startswith("AWS4-HMAC-SHA256 "))


if __name__ == "__main__":
    unittest.main()
