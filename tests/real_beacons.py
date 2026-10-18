"""Beacons captured from a real plug (firmware V2.3.0), with every identifying
field replaced by the fakes in beacon_factory.FAKE. The replacement edited the
ciphertext directly (XOR with old^new plaintext) and adjusted the plaintext
checksum, so all other bytes are exactly as the firmware produced them. These
pin the decoder against real device output, independent of protocol.py.
0x28 was set to 654321 (!= EcoID at 0x24) to pin which offset is the EcoID.
"""

REAL_ON = bytes.fromhex(
    "000000000055aa55aa0045434f20506c75677300085d1d2c3e5e1e2d43d59944dc56142d"
    "92a8102821b51b2993014007e476381bfa4a3515c87e0625ff4b3108fc580e09f35a3920"
    "c06e3a2138613722b70342559f681c129d7d41108e7e5e598f384a5a860a2138f00e2239"
    "f6025f3af406583bea3a5534e83e5635ee325336ec364c37e22a4930e02e4a31e6224732"
    "2f2631369ada7d0cf9bc1d69db943c66ade4740fa2fa4138a0fe423896c26f0a94c6680b"
    "8afa650488fe66058ef263068cf69c07d68fea74a0bef674e1e2970284e69003ba9a8d1c"
    "b89e8e1dbe928b1ebc96841fb08a8118b08f8219da5a1d2cb586b81baabab514a8beb615"
    "aeb2b316acb6ac17fffca9101230e9ee59220000"
)

REAL_OFF = bytes.fromhex(
    "000000000055aa55aa0045434f20506c75677300f8489044ce4b9345a869432c2c439945"
    "62bd9d40d1a096416314cd6f1463b5730a5fb87d386b8b4d0f5ebc600c4d8361034fb448"
    "307bb749c874ba4a4716cf3d6f7d917a6d68cc787e6bd3317f2dc732761fac50001baf51"
    "0617d2520413d5531a2fd85c182bdb5d1e27de5e1c23c15f123fc458103bc7591637ca5a"
    "df33bc5e6acff06409a990012b81b10e5df1f96752efcc5050ebcf5066d7e26264d3e563"
    "7aefe86c78ebeb6d7ee7ee6e7ce3116f269a671c50ab7b1c11f71a6a74f31d6b4a8f0074"
    "488b03754e8706764c830977409f0c70409a0f712a4f9044449335735aaf387c58ab3b7d"
    "5ea73e7e5ca3217f0fe92478e22564860b220000"
)
