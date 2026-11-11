import cv2


def deskew(img):
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    coords = cv2.findNonZero(cv2.bitwise_not(gray))
    angle = cv2.minAreaRect(coords)[-1]
    m = cv2.getRotationMatrix2D((img.shape[1] / 2, img.shape[0] / 2), angle, 1.0)
    return cv2.warpAffine(img, m, (img.shape[1], img.shape[0]))
