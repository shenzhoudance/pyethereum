import struct
import rlp
from utils import big_endian_to_int as idec
from utils import int_to_big_endian as _ienc

def ienc(x):
    "Ethereum(++) requires \x00 encoded as empty string"
    if x != 0: 
        return _ienc(x)
    else: 
        return ''

ienc4 = lambda x:struct.pack('>i', x) # 4 bytes big endian integer

def list_ienc(lst):
    "recursively big endian encode all integers in a list inplace"
    for i, e in enumerate(lst):
        if isinstance(e, list):
            lst[i] = list_ienc(e)
        elif isinstance(e, int):
            lst[i] = ienc(e)
    return lst


def dump_packet(packet):
    try:
        header = idec(packet[:4])
        payload_len = idec(packet[4:8])
        data = rlp.decode(packet[8:8+payload_len]) 
        cmd = WireProtocol.cmd_map.get(idec(data.pop(0)), 'unknown')
        return [header, payload_len, cmd] + data
    except:
        return ['DUMP Failed', packet]


class WireProtocol():

    """
    Translates between the network and the local data

    https://github.com/ethereum/wiki/wiki/%5BEnglish%5D-Wire-Protocol
    """

    cmd_map = dict(((0x00, 'Hello'),
                   (0x01, 'Disconnect'),
                   (0x02, 'Ping'),
                   (0x02, 'Pong'),
                   (0x10, 'GetPeers'),
                   (0x11, 'Peers'),
                   (0x12, 'Transactions'),
                   (0x13, 'Blocks'),
                   (0x14, 'GetChain'),
                   (0x15, 'NotInChain'),
                   (0x16, 'GetTransactions')))
    cmd_map_by_name = dict((v, k) for k, v in cmd_map.items())

    disconnect_reasons_map = dict((
        ('Disconnect requested', 0x00),
        ('TCP sub-system error', 0x01),
        ('Bad protocol', 0x02),
        ('Useless peer', 0x03),
        ('Too many peers', 0x04),
        ('Already connected', 0x05),
        ('Wrong genesis block', 0x06),
        ('Incompatible network protocols', 0x07),
        ('Client quitting', 0x08)))
    disconnect_reasons_map_by_id = \
        dict((v, k) for k, v in disconnect_reasons_map.items())

    SYNCHRONIZATION_TOKEN = 0x22400891
    PROTOCOL_VERSION = 0x08 # as sent by Ethereum(++)/v0.3.11/brew/Darwin/unknown
    NETWORK_ID = 0
    CLIENT_ID = 'Ethereum(py)/0.0.1'
    CAPABILITIES = 0x01 + 0x02 + 0x04 # node discovery + transaction relaying
    NODE_ID = None
    
    # NEED NODE_ID in order to work with Ethereum(++)/ 
    NODE_ID = 'J\x02U\xfaFs\xfa\xa3\x0f\xc5\xab\xfd<U\x0b\xfd\xbc\r<\x97=5\xf7&F:\xf8\x1cT\xa02\x81\xcf\xff"\xc5\xf5\x96[8\xacc\x01R\x98wW\xa3\x17\x82G\x85I\xc3o|\x84\xcbD6\xbay\xd6\xd9'

    def __init__(self, peermgr, config):
        self.peermgr = peermgr
        self.config = config

    def rcv_packet(self, peer, packet):
        """
        Though TCP provides a connection-oriented medium, Ethereum nodes communicate
        in terms of packets. These packets are formed as a 4-byte synchronisation token
        (0x22400891), a 4-byte "payload size", to be interpreted as a big-endian integer
        and finally an N-byte RLP-serialised data structure, where N is the aforementioned
        "payload size". To be clear, the payload size specifies the number of bytes in the
        packet ''following'' the first 8.
        """

        # check header
        if not packet.startswith(ienc(self.SYNCHRONIZATION_TOKEN)):
            print(self, 'check header failed')
            return self.send_Disconnect(peer, reason='Bad protocol')

        # unpack message
        payload_len = idec(packet[4:8])
        assert 8 + payload_len <= len(packet)
        data = rlp.decode(packet[8:8 + payload_len])

        # check cmd
        if (not len(data)) or (idec(data[0]) not in self.cmd_map):
            print(self, 'check cmd failed')
            return self.send_Disconnect(peer, reason='Bad protocol')

        cmd_id = idec(data.pop(0))
        func_name = "rcv_%s" % self.cmd_map[cmd_id]
        if not hasattr(self, func_name):
            print(self, 'unknown cmd', func_name)
            return
            """
            return self.send_Disconnect(
                peer,
                reason='Incompatible network protocols')
            raise NotImplementedError('%s not implmented')
            """
        # check Hello was sent

        # call the correspondig method
        return getattr(self, func_name)(peer, data)

    def send_packet(self, peer, data):
        """
        4-byte synchronisation token, (0x22400891),
        a 4-byte "payload size", to be interpreted as a big-endian integer
        an N-byte RLP-serialised data structure
        """
        payload = rlp.encode(list_ienc(data))
        packet = ienc4(self.SYNCHRONIZATION_TOKEN) 
        packet += ienc4(len(payload))
        packet += payload
        peer.send_packet(packet)

    def send_Hello(self, peer):
        # assert we did not sent hello yet
        payload = [0x00,
                   self.PROTOCOL_VERSION,
                   self.NETWORK_ID,
                   self.CLIENT_ID,
                   self.config.getint('server', 'port'),
                   self.CAPABILITIES]                   
        if self.NODE_ID:
            payload.append(self.NODE_ID)
        self.send_packet(peer, payload)

        peer.hello_sent = True

    def rcv_Hello(self, peer, data):
        """
        [0x00, PROTOCOL_VERSION, NETWORK_ID, CLIENT_ID, CAPABILITIES, LISTEN_PORT, NODE_ID]
        First packet sent over the connection, and sent once by both sides.
        No other messages may be sent until a Hello is received.
        PROTOCOL_VERSION is one of:
            0x00 for PoC-1;
            0x01 for PoC-2;
            0x07 for PoC-3.
            0x08 sent by Ethereum(++)/v0.3.11/brew/Darwin/unknown
        NETWORK_ID should be 0.
        CLIENT_ID Specifies the client software identity, as a human-readable string
                    (e.g. "Ethereum(++)/1.0.0").
        CAPABILITIES specifies the capabilities of the client as a set of flags;
                    presently three bits are used:
                    0x01 for peers discovery, 0x02 for transaction relaying, 0x04 for block-chain querying.        
        LISTEN_PORT specifies the port that the client is listening on
                    (on the interface that the present connection traverses).
                    If 0 it indicates the client is not listening.
        NODE_ID is optional and specifies a 512-bit hash, (potentially to be used as public key)
                    that identifies this node.
        
        [574621841, 116, 'Hello', '\x08', '', 'Ethereum(++)/v0.3.11/brew/Darwin/unknown', '\x07', 'v_', "\xc5\xfe\xc6\xea\xe4TKvz\x9e\xdc\xa7\x01\xf6b?\x7fB\xe7\xfc(#t\xe9}\xafh\xf3Ot'\xe5u\x07\xab\xa3\xe5\x95\x14 |P\xb0C\xa2\xe4jU\xc8z|\x86\xa6ZV!Q6\x82\xebQ$4+"]
        [574621841, 27, 'Hello', '\x08', '\x00', 'Ethereum(py)/0.0.1', 'vb', '\x07']
        """

        # check compatibility
        if idec(data[0]) != self.PROTOCOL_VERSION:
            return self.send_Disconnect(
                peer,
                reason='Incompatible network protocols')

        if idec(data[1]) != self.NETWORK_ID:
            return self.send_Disconnect(peer, reason='Wrong genesis block')

        """
        spec has CAPABILITIES after PORT, CPP client the other way round. emulating the latter
        https://github.com/ethereum/cpp-ethereum/blob/master/libethereum/PeerNetwork.cpp#L144
        """


        # TODO add to known peers list
        peer.hello_received = True

        # reply with hello if not send
        if not peer.hello_sent:
            self.send_Hello(peer)

    def send_Ping(self, peer):
        """
        [0x02]
        Requests an immediate reply of Pong from the peer.
        """
        self.send_packet(peer, [0x02])

    def rcv_Ping(self, peer, data):
        self.send_Pong(peer)

    def send_Pong(self, peer):
        """
        [0x03]
        Reply to peer's Ping packet.
        """
        self.send_packet(peer, [0x03])

    def rcv_Pong(self, peer, data):
        pass

    def send_Disconnect(self, peer, reason=None):
        """
        [0x01, REASON]
        Inform the peer that a disconnection is imminent;
        if received, a peer should disconnect immediately.
        When sending, well-behaved hosts give their peers a fighting chance
        (read: wait 2 seconds) to disconnect to before disconnecting themselves.
        REASON is an optional integer specifying one of a number of reasons
        """
        print(self, 'sending disconnect because', reason)
        assert not reason or reason in self.disconnect_reasons_map
        payload = [0x01]
        if reason:
            payload.append(self.disconnect_reasons_map[reason])
        self.send_packet(peer, payload)

    def rcv_Disconnect(self, peer, data):
        if len(data):
            reason = self.disconnect_reasons_map_by_id[idec(data[0])]
        self.peermgr.remove_peer(peer)
